"""检索过滤策略（离线，不连库）：少命中不回退、BM25 结果也按型号/批次收敛。

旧策略「过滤后命中 < min_filtered_hits 就整批回退全库」会把 1–2 条精确匹配丢掉，
换成一大堆全库引用（真实踩坑：BK系列+B22 只命中 2 条，却显示"过滤后命中不足，
已回退全库"）。新策略只有 0 命中才回退；少量命中保留并提示样本量。
"""

from __future__ import annotations

from dataclasses import dataclass

from modules.retrieval.pipeline import RetrievalPipeline, RetrievalSettings
from modules.retrieval.types import ScoredChunk


class _FixedEmbedder:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    def embed_one(self, text: str) -> list[float]:
        return self.vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vector for _text in texts]


@dataclass
class _FakeIndex:
    results: list[ScoredChunk]

    def search(self, tokens, *, top_k):  # noqa: ANN001 - 测试桩
        return self.results[:top_k]


class _FakeCache:
    def __init__(self, results: list[ScoredChunk]) -> None:
        self.index = _FakeIndex(results)

    def get(self, conn):  # noqa: ANN001 - 测试桩
        return self.index


def _pipeline(bm25_results: list[ScoredChunk] | None = None) -> RetrievalPipeline:
    return RetrievalPipeline(
        settings=RetrievalSettings(top_k_dense=20, top_k_bm25=20, final_top_n=8),
        embedder=_FixedEmbedder([1.0, 0.0, 0.0]),
        bm25_cache=_FakeCache(bm25_results or []),
    )


def _chunk(cid: int, evidence_id: str, *, model=None, batch=None, content="正文") -> ScoredChunk:
    return ScoredChunk(
        chunk_id=cid,
        evidence_id=evidence_id,
        content=content,
        model=model,
        batch=batch,
    )


def test_small_filtered_hit_set_is_kept_not_fallen_back(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_dense(conn, vector, *, top_k, model=None, batch=None, model_aliases=None):  # noqa: ANN001
        calls.append((model, batch))
        if model or batch:
            return [
                _chunk(1, "T-A", model="BK", batch="B22", content="BK 手册正文"),
                _chunk(2, "T-B", model="BK系列", content="BK系列手册正文"),
            ]
        return [
            _chunk(3, "T-X", content="全库正文 X"),
            _chunk(4, "T-Y", content="全库正文 Y"),
            _chunk(5, "T-Z", content="全库正文 Z"),
        ]

    monkeypatch.setattr("modules.retrieval.pipeline.dense_search", fake_dense)
    result = _pipeline().retrieve(
        None,
        question="制动器间隙",
        model="BK系列",
        batch="B22",
        model_aliases=("BK系列", "BK"),
    )

    assert len(calls) == 1, "只有 2 条精确命中时也必须保留过滤结果，不能回退全库"
    assert {item.evidence_id for item in result.items} == {"T-A", "T-B"}
    assert result.filter_note and "未回退全库" in result.filter_note


def test_zero_filtered_hits_still_fall_back(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_dense(conn, vector, *, top_k, model=None, batch=None, model_aliases=None):  # noqa: ANN001
        calls.append((model, batch))
        if model or batch:
            return []
        return [_chunk(3, "T-X", content="全库正文 X"), _chunk(4, "T-Y", content="全库正文 Y")]

    monkeypatch.setattr("modules.retrieval.pipeline.dense_search", fake_dense)
    result = _pipeline().retrieve(None, question="制动器间隙", model="BK系列", batch="B22")

    assert len(calls) == 2, "过滤后 0 命中才允许回退全库"
    assert result.filter_note and "已回退全库" in result.filter_note


def test_bm25_hits_are_filtered_by_provenance(monkeypatch) -> None:
    def fake_dense(conn, vector, *, top_k, model=None, batch=None, model_aliases=None):  # noqa: ANN001
        return [_chunk(1, "T-A", model="BK", batch="B22")]

    monkeypatch.setattr("modules.retrieval.pipeline.dense_search", fake_dense)
    # BM25 不过滤：它召回了一条别的批次、以及一条别的型号
    bm25 = [
        _chunk(9, "T-OTHER-BATCH", model="BK", batch="B23", content="串批次的引用"),
        _chunk(10, "T-OTHER-MODEL", model="DB350", batch="B22", content="串型号的引用"),
        _chunk(11, "T-NULL-BATCH", model="BK系列", batch=None, content="不限批次"),
    ]
    result = _pipeline(bm25).retrieve(
        None,
        question="制动器间隙",
        model="BK系列",
        batch="B22",
        model_aliases=("BK系列", "BK"),
    )

    ids = {item.evidence_id for item in result.items}
    assert "T-A" in ids
    assert "T-NULL-BATCH" in ids, "批次为空的切片表示不限批次，应保留"
    assert "T-OTHER-BATCH" not in ids, "其它批次的 BM25 命中必须被过滤掉"
    assert "T-OTHER-MODEL" not in ids, "其它型号的 BM25 命中必须被过滤掉"
