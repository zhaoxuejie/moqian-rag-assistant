"""证据门（Evidence Gate）：回答前的最后一道"够不够格"判断。

为什么必须由代码判定而不是提示词：模型永远愿意"尽力回答"，但产品纪律是
**证据不足必须拒答**（PRD §5.2）。所以这里用可解释的三条硬指标：

1. `hits`：融合后命中的证据条数 ≥ `min_hits`；
2. `top_score`：向量最高相似度 ≥ `min_score`（LLM 幻觉高发区的近似判据）；
3. `coverage`：查询词被命中证据覆盖的比例 ≥ `min_coverage`（防止"只搜到一个词就答"）。

不过就返回原因 `gap`，由 app 层转成 `gap` 事件并计入知识缺口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class GateItem(Protocol):
    """证据门只关心两件事：正文与向量分。

    用 Protocol 而不是 import retrieval 的 `ScoredChunk`：`modules` 之间不允许互相 import
    （架构 §4.1），这里用结构化类型解耦，retrieval 的结果对象天然满足。
    """

    content: str
    dense_score: float | None


@dataclass
class GateResult:
    passed: bool
    reason: str | None
    """`None` 表示通过；否则为拒答原因（当前只有 `gap`）。"""
    hits: int
    top_score: float | None
    coverage: float
    missing_terms: list[str] = field(default_factory=list)


def _distinct_terms(terms: list[str]) -> list[str]:
    """去掉被更长词包含的短词（jieba 搜索模式会产出"计算/算机/计算机"这类重叠）。"""
    ordered = sorted(set(terms), key=len, reverse=True)
    return [
        term
        for term in ordered
        if not any(term != other and term in other for other in ordered)
    ]


def evaluate_gate(
    items: list[GateItem],
    *,
    query_tokens: list[str],
    min_hits: int,
    min_score: float,
    min_coverage: float,
) -> GateResult:
    if not items:
        return GateResult(
            passed=False,
            reason="gap",
            hits=0,
            top_score=None,
            coverage=0.0,
            missing_terms=list(query_tokens),
        )

    hits = len(items)
    dense_scores = [item.dense_score for item in items if item.dense_score is not None]
    top_score = max(dense_scores) if dense_scores else None

    # 覆盖度：查询词（去重、去单字噪声）在命中证据正文里是否出现
    terms = [token for token in dict.fromkeys(query_tokens) if len(token) >= 2]
    if terms:
        haystack = "\n".join(item.content for item in items)
        matched = [term for term in terms if term in haystack]
        coverage = len(matched) / len(terms)
        missing = _distinct_terms([term for term in terms if term not in matched])
    else:
        coverage = 1.0 if hits else 0.0
        missing = []

    if hits < min_hits:
        return GateResult(False, "gap", hits, top_score, coverage, missing)
    if top_score is not None and top_score < min_score:
        return GateResult(False, "gap", hits, top_score, coverage, missing)
    if coverage < min_coverage:
        return GateResult(False, "gap", hits, top_score, coverage, missing)
    return GateResult(True, None, hits, top_score, coverage, missing)
