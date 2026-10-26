"""检索层公共数据结构。

一次检索会经过三路（dense / bm25 / 融合），它们描述的是同一件事：
"这个切片为什么被选中、得分为多少"。所以统一用 `ScoredChunk` 承载，
只是 `dense_score` / `bm25_score` / `rrf_score` 三列按路径填充 —— 便于日志复盘
"这条证据是向量召回的，还是关键词召回的"（PRD §5.2 可解释性）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScoredChunk:
    chunk_id: int
    evidence_id: str
    content: str
    section_no: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    batch: str | None = None
    version: str | None = None
    model: str | None = None
    loc: str | None = None
    """出处原文（Excel/FAQ 是「工作表 第N行」；PDF 可能是 §3.1）。有值时优先于拼装值。"""
    dense_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    sources: list[str] = field(default_factory=list)
    """命中的召回路径：`dense` / `bm25` / `both`，用于解释与调参。"""

    def to_contract(self) -> dict:
        """转成契约 `Citation` 的形状（后台日志与 SSE 共用）。"""
        loc_parts: list[str] = []
        if self.loc:
            # 已有出处原文（工作表行号 / 章节号）就直接用，避免再拼一次
            loc_parts.append(self.loc)
        else:
            if self.section_no:
                loc_parts.append(f"§{self.section_no}")
            if self.page_from:
                page = (
                    f"P{self.page_from}"
                    if not self.page_to or self.page_to == self.page_from
                    else f"P{self.page_from}–{self.page_to}"
                )
                loc_parts.append(page)
        return {
            "evidenceId": self.evidence_id,
            "kind": "manual",
            "title": f"《维修手册》{('§' + self.section_no) if self.section_no else ''}".strip(),
            "loc": " · ".join(loc_parts) if loc_parts else None,
            "batch": self.batch,
            "score": (
                self.rerank_score
                if self.rerank_score is not None
                else (self.dense_score if self.dense_score is not None else self.rrf_score)
            ),
        }


@dataclass
class RetrievalResult:
    items: list[ScoredChunk]
    dense_count: int
    bm25_count: int
    fused_count: int
    query_tokens: list[str]
    filter_note: str | None = None
    """过滤说明（如"未取得设备上下文，已全库检索"），直接进 `structured.filterNote`。"""
    reranked: bool = False
    """是否实际应用了 cross-encoder 重排（失败/未配置为 False）。"""

    def evidence_ids(self) -> list[str]:
        return [item.evidence_id for item in self.items]
