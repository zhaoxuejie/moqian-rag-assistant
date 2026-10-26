"""检索落库联调（需要数据库 + S2 已入库语料）。

用真实切片验证"检索能召回、能融合、证据门能拒答"：
- 用某条切片自己的向量做查询，dense 的 top1 必须是它自己（余弦=1，确定性）；
- BM25 用真实中文分词，查"制动器 间隙"应召回含"间隙"的切片；
- 管线拼好后查询词覆盖率不足时，证据门必须拒答。
"""

from __future__ import annotations

import pytest
from conftest import FakeEmbedder, requires_db

from modules.evidence import evaluate_gate
from modules.retrieval import Bm25IndexCache, RetrievalPipeline, RetrievalSettings
from modules.retrieval.dense import dense_search

pytestmark = requires_db


@pytest.fixture()
def chunk_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("select count(*) from chunks where disabled = false and embedding is not null")
        return int(cur.fetchone()[0])


@pytest.fixture()
def paged_chunk_count(conn) -> int:
    """带页码出处（page_from > 0）的切片数：只有手册类语料才有页码。"""
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from chunks "
            "where disabled = false and embedding is not null and page_from > 0"
        )
        return int(cur.fetchone()[0])


@pytest.fixture(autouse=True)
def require_real_corpus(chunk_count: int, paged_chunk_count: int) -> None:
    """没有入库语料就整组跳过。

    这套用例验证的是"真实语料上的召回与拒答"，前提是本机跑过
    `python -m app.import_docs` 灌过手册；而原件不入版本库（D-029 / data-layout §5），
    CI 的 data/ 是空的，没有语料可查。环境不具备时跳过，不让 CI 红。

    注意：只用"有切片"判断不够——FAQ 表格入库的切片没有页码（page_from=0），
    而本组用例断言"检索结果必须带页码出处"。只灌了 FAQ 的库里这些断言必然失败，
    所以还要确认存在带页码的手册切片。
    """
    if chunk_count <= 0:
        pytest.skip("无真实语料（本机先 python -m app.import_docs）；CI 无 data/ 自动跳过")
    if paged_chunk_count <= 0:
        pytest.skip("语料里没有带页码的手册切片（只有 FAQ 等无页码来源），页码断言不适用")


def test_corpus_is_ingested(chunk_count: int) -> None:
    assert chunk_count > 0, "先跑 python -m app.import_docs 再跑本用例"


def _first_manual_chunk(conn) -> tuple[int, str, str, str]:
    """取第一条带页码的手册切片：(id, evidence_id, embedding 文本, 正文)。

    语料可能混有 FAQ（page_from=0，出处是工作表行号），而“页码出处”只对手册成立，
    所以这里显式挑手册切片，不假设 id 最小的那条一定是手册。
    """
    with conn.cursor() as cur:
        cur.execute(
            "select id, evidence_id, embedding::text, content from chunks "
            "where disabled = false and embedding is not null and page_from > 0 "
            "order by id limit 1"
        )
        return cur.fetchone()


def test_dense_search_returns_itself_for_own_vector(conn, chunk_count: int) -> None:
    chunk_id, evidence_id, vector_text, _content = _first_manual_chunk(conn)
    vector = [float(value) for value in vector_text.strip("[]").split(",")]

    results = dense_search(conn, vector, top_k=3)
    assert results, "向量检索应返回结果"
    assert results[0].chunk_id == chunk_id, "用自己的向量查，top1 必须是它自己"
    assert results[0].evidence_id == evidence_id
    assert results[0].dense_score == pytest.approx(1.0, abs=1e-3)
    assert results[0].page_from, "手册切片的页码出处不能在检索链路上丢掉"


def test_bm25_index_over_real_corpus(conn, chunk_count: int) -> None:
    from modules.retrieval import tokenize_query

    index = Bm25IndexCache().get(conn)
    results = index.search(tokenize_query("制动器间隙标准值"), top_k=5)
    assert results, "真实语料应能关键词召回"
    assert any("间隙" in item.content for item in results)
    assert results[0].bm25_score > 0


def test_pipeline_full_recall_with_known_vector(conn, chunk_count: int) -> None:
    chunk_id, _evidence_id, vector_text, content = _first_manual_chunk(conn)
    vector = [float(value) for value in vector_text.strip("[]").split(",")]

    pipeline = RetrievalPipeline(
        settings=RetrievalSettings(top_k_dense=10, top_k_bm25=10, final_top_n=5),
        embedder=FakeEmbedderFromVector(vector),
        bm25_cache=Bm25IndexCache(),
    )
    # 用切片正文当问题，确保 BM25 也能命中
    result = pipeline.retrieve(conn, question=content[:40])
    assert result.items
    assert result.dense_count > 0 and result.fused_count > 0
    assert result.filter_note and "全库" in result.filter_note
    # 混合语料（手册 + FAQ）下不要求每条都有页码，但手册切片的页码不能在链路里丢
    own = [item for item in result.items if item.chunk_id == chunk_id]
    assert own, "用某条切片的正文检索，它自己应在结果里"
    assert own[0].page_from, "手册切片的页码出处不能在检索链路上丢掉"


def test_pipeline_gate_rejects_unrelated_question(conn, chunk_count: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "select embedding::text from chunks where embedding is not null order by id limit 1"
        )
        vector_text = cur.fetchone()[0]
    vector = [float(value) for value in vector_text.strip("[]").split(",")]

    pipeline = RetrievalPipeline(
        settings=RetrievalSettings(final_top_n=5),
        embedder=FakeEmbedderFromVector(vector),
    )
    result = pipeline.retrieve(conn, question="量子计算机的退相干时间怎么测量")
    gate = evaluate_gate(
        result.items,
        query_tokens=result.query_tokens,
        min_hits=1,
        min_score=0.35,
        min_coverage=0.5,
    )
    assert gate.passed is False, "与语料无关的问题必须被证据门拒绝"
    assert gate.reason == "gap"


class FakeEmbedderFromVector:
    """固定返回给定向量：用于把 dense 检索变成确定性测试。"""

    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    def embed_one(self, _text: str) -> list[float]:
        return self.vector


def test_fake_embedder_is_compatible() -> None:
    """保证 conftest 的假 embedder 与管线协议一致（避免协议漂移）。"""
    assert FakeEmbedder().embed_one("x") and len(FakeEmbedder().embed_one("x")) == 1024
