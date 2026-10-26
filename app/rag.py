"""S3 的 RAG 编排件：把检索结果变成提示词，把模型输出拆成步骤并校验引用。

这一层只做"拼接与解析"，不做检索（modules/retrieval）也不做判定（modules/evidence）。
提示词里的行业约束放在 `domain.py`（纪律 6：词汇不进 modules）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from modules.citation import extract_citations, validate_citations
from modules.retrieval import RetrievalResult, ScoredChunk

from .config import Settings
from .domain import CONFLICT_MESSAGE, RAG_SYSTEM_PROMPT

_STEP_PREFIX = re.compile(r"^\s*(?:步骤\s*)?\d+\s*[.、)．]\s*")
MAX_STEPS = 8


@dataclass
class RagStep:
    index: int
    text: str
    citations: list[str] = field(default_factory=list)


@dataclass
class RagAnswer:
    steps: list[RagStep]
    citations: list[ScoredChunk]
    stripped: list[str]
    refused: bool


def build_retrieval_config(settings: Settings, overrides: dict | None = None):
    """延迟导入，避免 app 模块加载时就和 modules 互相牵制。

    `overrides` 来自 `app_config.config.retrieval`（D-012）；非法值回退环境变量。
    """
    from modules.retrieval import RetrievalSettings

    data = overrides or {}

    def _int(key: str, default: int) -> int:
        value = data.get(key)
        try:
            return int(value) if value not in (None, "") else default
        except (TypeError, ValueError):
            return default

    return RetrievalSettings(
        top_k_dense=_int("recallPerRoute", settings.retrieval_top_k_dense),
        top_k_bm25=_int("recallPerRoute", settings.retrieval_top_k_bm25),
        final_top_n=_int("topK", settings.retrieval_final_top_n),
        rrf_k=_int("rrfK", settings.retrieval_rrf_k),
        min_filtered_hits=settings.retrieval_min_filtered_hits,
        reranker_provider=settings.reranker_provider,
        rerank_candidates=settings.retrieval_rerank_candidates,
    )


def format_evidence(items: list[ScoredChunk]) -> str:
    blocks: list[str] = []
    for item in items:
        loc_parts: list[str] = []
        if item.section_no:
            loc_parts.append(f"§{item.section_no}")
        if item.page_from:
            loc_parts.append(
                f"P{item.page_from}"
                if not item.page_to or item.page_to == item.page_from
                else f"P{item.page_from}–{item.page_to}"
            )
        loc = " · ".join(loc_parts)
        header = f"[{item.evidence_id}]" + (f"（{loc}）" if loc else "")
        blocks.append(f"{header}\n{item.content}")
    return "\n\n".join(blocks)


def build_messages(
    question: str, retrieval: RetrievalResult, structured: dict
) -> list[dict[str, str]]:
    model = structured.get("model") or "未提供"
    batch = structured.get("batch") or "未提供"
    user_content = (
        f"设备型号：{model}\n批次：{batch}\n"
        f"问题：{question}\n\n【证据】\n{format_evidence(retrieval.items)}"
    )
    return [
        {"role": "system", "content": RAG_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_answer(answer: str, *, allowed_items: list[ScoredChunk]) -> RagAnswer:
    """把模型输出拆成步骤，并逐条校验引用（非法引用整条剥离）。

    `allowed_items` 必须传**本轮证据集**，不能传全库：引用校验的意义就是
    "只能说这一轮检索到的东西"。
    """
    allowed = {item.evidence_id: item for item in allowed_items}
    lines = [line.strip() for line in (answer or "").splitlines() if line.strip()]
    steps: list[RagStep] = []
    stripped: list[str] = []

    for line in lines:
        text = _STEP_PREFIX.sub("", line).strip()
        if not text:
            continue
        check = validate_citations(text, allowed)
        stripped.extend(check.stripped)
        if not check.text:
            continue
        steps.append(RagStep(index=len(steps) + 1, text=check.text, citations=check.kept))

    if not steps:  # 模型只回了一行且没有被识别成步骤
        check = validate_citations(answer.strip(), allowed)
        stripped.extend(check.stripped)
        if check.text:
            steps.append(RagStep(index=1, text=check.text, citations=check.kept))

    # 引用清单按"步骤里首次出现顺序"排列（契约要求前端不再另编号）
    # 末尾"无引用且像被截断"的步骤直接丢掉：步骤以引用为锚，截断尾巴不展示
    while steps and not steps[-1].citations:
        steps.pop()
    # 提示词要求最多 8 步，但模型偶尔超发；契约与界面按 8 步设计，这里硬截断
    if len(steps) > MAX_STEPS:
        steps = steps[:MAX_STEPS]
    for position, step in enumerate(steps, start=1):
        step.index = position

    ordered_ids = list(dict.fromkeys(cid for step in steps for cid in step.citations))
    citations = [allowed[cid] for cid in ordered_ids]
    return RagAnswer(steps=steps, citations=citations, stripped=stripped, refused=not citations)


def detect_conflict(items: list[ScoredChunk], *, top_n: int = 3) -> dict | None:
    """同一型号命中多个批次的资料 → 并列展示，系统不替工程师选择（纪律 7）。"""
    head = items[:top_n]
    batches: dict[str, list[str]] = {}
    for item in head:
        if item.batch:
            batches.setdefault(item.batch, []).append(item.evidence_id)
    if len(batches) < 2:
        return None
    options = [
        {"label": f"批次 {batch}", "evidenceIds": evidence_ids}
        for batch, evidence_ids in batches.items()
    ]
    return {"conflict": CONFLICT_MESSAGE, "options": options}


def all_citation_ids(answer: str) -> list[str]:
    return extract_citations(answer)
