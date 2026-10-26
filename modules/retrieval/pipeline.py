"""检索管线：向量 + BM25 → RRF → topN。

与 `app` 的分工：
- 机制在这里：怎么召回、怎么融合、过滤后命中不足怎么回退；
- 行业词汇在 app：查询怎么从自然语言捏出来（提示词）、型号/批次叫什么、阈值多少。

重排（cross-encoder）当前是**可插拔但默认关闭**：本机 Ollama 的 reranker 实测不可用
（加载崩溃 / 分词损坏，见 decisions.md D15）。`rerank_candidates` 先保留为候选窗口，
将来接上重排时只换这一层。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import psycopg

from .bm25 import Bm25IndexCache, tokenize_query
from .dense import dense_search
from .fusion import DEFAULT_RRF_K, dedupe_by_content, rrf_fuse
from .types import RetrievalResult

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    def embed_one(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    def rerank(self, query: str, documents: list[str]): ...


@dataclass
class RetrievalSettings:
    """检索参数（由 app 层从配置注入，模块不读环境变量）。"""

    top_k_dense: int = 20
    top_k_bm25: int = 20
    final_top_n: int = 6
    rrf_k: int = DEFAULT_RRF_K
    min_filtered_hits: int = 3
    reranker_provider: str = "none"
    rerank_candidates: int = 30


class RetrievalPipeline:
    def __init__(
        self,
        *,
        settings: RetrievalSettings,
        embedder: Embedder,
        bm25_cache: Bm25IndexCache | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self.bm25_cache = bm25_cache or Bm25IndexCache()
        self.reranker = reranker

    def retrieve(
        self,
        conn: psycopg.Connection,
        *,
        question: str,
        model: str | None = None,
        batch: str | None = None,
        model_aliases: Sequence[str] | None = None,
    ) -> RetrievalResult:
        tokens = tokenize_query(question)
        vector = self.embedder.embed_one(question)

        dense = dense_search(
            conn,
            vector,
            top_k=self.settings.top_k_dense,
            model=model,
            batch=batch,
            model_aliases=model_aliases,
        )
        filter_note: str | None = None
        filtered = bool(model or batch)
        fell_back = False
        if filtered and not dense:
            # 过滤后**一条都没有**才回退全库（≠ 少量命中也被丢弃：那会把精确匹配稀释掉）
            dense = dense_search(conn, vector, top_k=self.settings.top_k_dense)
            fell_back = True
            filter_note = "按型号/批次过滤后无命中，已回退全库检索——请核对设备型号与批次"
        elif filtered and len(dense) < self.settings.min_filtered_hits:
            # 少量精确命中：保留结果、明确告知样本量不足，而不是换成一堆全库结果
            filter_note = (
                f"按型号/批次精确匹配仅 {len(dense)} 条（未回退全库）——结果可能不完整"
            )
        elif not filtered:
            filter_note = "未取得设备上下文，已全库检索——请注意核对设备型号与批次后再执行"

        index = self.bm25_cache.get(conn)
        bm25 = index.search(tokens, top_k=self.settings.top_k_bm25)

        fused = rrf_fuse(
            dense,
            bm25,
            k=self.settings.rrf_k,
        )
        if filtered and not fell_back:
            # BM25 不支持型号/批次过滤，融合后必须再筛一遍，避免引用串批次/串型号；
            # NULL 仍按「不限」处理（与 dense 口径一致）。
            allowed_models = {
                alias for alias in (model, *(model_aliases or ())) if alias
            }

            def _provenance_ok(item) -> bool:  # noqa: ANN001 - 内部谓词
                if batch and item.batch not in (None, batch):
                    return False
                if model and item.model not in allowed_models and item.model not in (None, ""):
                    return False
                return True

            fused = [item for item in fused if _provenance_ok(item)]
        # cross-encoder 精排：只对候选窗口内重排；任何失败都回退 RRF，不阻断回答
        reranked = False
        if self.reranker is not None and fused:
            candidates = fused[: self.settings.rerank_candidates]
            try:
                result = self.reranker.rerank(
                    question, [item.content for item in candidates]
                )
                scores = list(getattr(result, "scores", []) or [])
                if len(scores) == len(candidates):
                    order = sorted(
                        range(len(candidates)), key=lambda i: scores[i], reverse=True
                    )
                    candidates = [candidates[i] for i in order]
                    for item, score in zip(
                        candidates, (scores[i] for i in order), strict=True
                    ):
                        item.rerank_score = float(score)
                    fused = candidates + fused[len(candidates) :]
                    reranked = True
                else:
                    logger.warning(
                        "reranker returned %d scores for %d candidates; fallback RRF",
                        len(scores),
                        len(candidates),
                    )
            except Exception as exc:  # noqa: BLE001 - 重排失败必须降级
                logger.warning("rerank failed, fallback RRF: %s", exc)
        # 先按正文去重（多份手册共用模板会产出完全相同的切片），再截断到证据集大小
        fused = dedupe_by_content(fused, limit=self.settings.final_top_n)
        return RetrievalResult(
            items=fused,
            dense_count=len(dense),
            bm25_count=len(bm25),
            fused_count=len(fused),
            query_tokens=tokens,
            filter_note=filter_note,
            reranked=reranked,
        )
