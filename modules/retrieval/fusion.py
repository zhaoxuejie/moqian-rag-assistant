"""RRF（Reciprocal Rank Fusion）融合两路召回。

为什么用 RRF 而不是"分数加权"：向量余弦分与 BM25 分**量纲不同**，直接加权要调系数、
换语料还要重调。RRF 只看名次：`score = Σ 1/(k + rank)`，k=60 是行业常用值，
对两路召回质量差异不敏感，是"先跑通"阶段最稳的融合方式。

融合后保留两路原始分数与来源标记（`dense_score` / `bm25_score` / `sources`），
后台复盘时能看出"这条是向量召回的、那条是关键词召回的"。
"""

from __future__ import annotations

from .types import ScoredChunk

DEFAULT_RRF_K = 60


def rrf_fuse(
    dense: list[ScoredChunk],
    bm25: list[ScoredChunk],
    *,
    k: int = DEFAULT_RRF_K,
    limit: int | None = None,
) -> list[ScoredChunk]:
    """把两路召回按名次融合，返回按 RRF 分降序的结果。"""
    merged: dict[int, ScoredChunk] = {}
    scores: dict[int, float] = {}

    def absorb(items: list[ScoredChunk], source: str) -> None:
        for rank, item in enumerate(items, start=1):
            scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + 1.0 / (k + rank)
            existing = merged.get(item.chunk_id)
            if existing is None:
                item.sources = sorted({*item.sources, source})
                merged[item.chunk_id] = item
                continue
            # 同一块被两路命中：合并不重复的字段，标 both
            existing.sources = sorted({*existing.sources, source})
            if item.dense_score is not None and existing.dense_score is None:
                existing.dense_score = item.dense_score
            if item.bm25_score is not None and existing.bm25_score is None:
                existing.bm25_score = item.bm25_score
            for field_name in (
                "section_no",
                "page_from",
                "page_to",
                "batch",
                "version",
                "model",
                "loc",
            ):
                if getattr(existing, field_name) in (None, "") and getattr(item, field_name):
                    setattr(existing, field_name, getattr(item, field_name))

    absorb(dense, "dense")
    absorb(bm25, "bm25")

    result = list(merged.values())
    for item in result:
        item.rrf_score = scores[item.chunk_id]
        if len(item.sources) > 1:
            item.sources = ["both"]
    result.sort(key=lambda item: item.rrf_score or 0.0, reverse=True)
    return result[:limit] if limit else result


def dedupe_by_content(items: list[ScoredChunk], *, limit: int) -> list[ScoredChunk]:
    """按正文去重后取前 `limit` 条。

    为什么需要：15 份手册共用同一套模板，同一段"维护周期"文字会以 8–15 个切片出现。
    这些切片信息完全相同，如果不去重，RRF 前几名会被它们占满，真正命中问题词的
    切片（哪怕 BM25 排第一）会被挤出证据集——实测"钢丝绳检查"就是被 8 个重复的
    §3.1 切片挤掉的。去重后保留排名最高的那一条，保证证据集有信息多样性。
    """
    seen: set[str] = set()
    kept: list[ScoredChunk] = []
    for item in items:
        key = " ".join(item.content.split())
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
        if len(kept) >= limit:
            break
    return kept
