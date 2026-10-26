"""问答管线核心（S6 抽出来，供 `/chat` 与评测 runner **复用同一套代码**，ADR9）。

为什么单独一层：`/chat` 要的是"SSE 帧 + 落日志"，评测要的是"结果对象 + 指标"。
如果评测另写一遍检索/证据门/引用校验，就失去"评测反映线上"的意义。所以这里只做
**一次问答的计算**，不碰 SSE、不写 messages 表；事件与日志留在 `chat_service`。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import psycopg

from modules.evidence import GateResult, evaluate_gate
from modules.llm import LlmError
from modules.retrieval import RetrievalPipeline, RetrievalResult, ScoredChunk

from .config import Settings
from .domain import (
    ASK_BATCH_BODY,
    ASK_BATCH_TITLE,
    BATCH_SENSITIVE_HINTS,
    GAP_BODY,
    SAFETY_BODY,
    model_match_aliases,
)
from .rag import RagStep, build_messages, build_retrieval_config, detect_conflict, parse_answer
from .runtime_config import load_config
from .schemas import ClarifySelection, HostContext

logger = logging.getLogger(__name__)


@dataclass
class TurnOutcome:
    question: str
    structured: dict[str, Any]
    filter_note: str | None = None
    safety_word: str | None = None
    retrieval: RetrievalResult | None = None
    gate: GateResult | None = None
    conflict: dict | None = None
    ask: dict | None = None
    steps: list[RagStep] = field(default_factory=list)
    citations: list[ScoredChunk] = field(default_factory=list)
    stripped: list[str] = field(default_factory=list)
    refused: bool = False
    gate_reason: str | None = None
    """`safe` / `gap` / `citation_stripped` / `error`；`None` 表示正常回答。"""
    content: str = ""
    error: Exception | None = None
    elapsed_ms: int = 0


def build_structured(
    question: str,
    *,
    context: HostContext | None = None,
    clarify: ClarifySelection | None = None,
) -> dict[str, Any]:
    """结构化条件：只取宿主上下文与用户确认，缺什么就空着，不猜（纪律 2）。"""
    model = context.device_model if context else None
    batch = context.batch if context else None
    batch_origin = "host_context" if batch else None
    if clarify:
        if clarify.field == "batch":
            if clarify.value == "unknown":
                batch = None
                batch_origin = "user_uncertain"
            else:
                batch = clarify.value
                batch_origin = "user_confirmed"
        elif clarify.field == "model":
            model = clarify.value
    missing = [name for name, value in (("model", model), ("batch", batch)) if not value]
    return {
        "symptom": question,
        "model": model,
        "batch": batch,
        "code": None,
        "missing": missing,
        "batchOrigin": batch_origin,
    }


def match_safety_word(question: str, words: tuple[str, ...]) -> str | None:
    for word in words:
        if word and word in question:
            return word
    return None


_COVERAGE_STOPWORDS = frozenset(    {
        # 疑问/请求类
        "怎么", "如何", "多少", "什么", "为什么", "哪种", "哪些", "是否", "可以", "需要", "请问",
        # 口语化时间与指代：工程师口述里常见，但不是证据里的事实词。
        # 不剔除会让"之前那台梯门关到一半又弹开，怎么修好的"这种真实问法卡在覆盖率 0.43 上。
        "之前", "上次", "后来", "最后", "那台", "这台", "当时", "记得", "案例", "修好",
    }
)


def coverage_tokens(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token not in _COVERAGE_STOPWORDS]


# 故障码（HY-E001 这类）：由「故障码处置手册摘编」直接回答，正文已按批次写明参数，
# 不再单独弹批次追问——否则"HY-E013 平层感应器故障"因为含"平层"会被误拦。
_FAULT_CODE_PATTERN = re.compile(r"HY-[A-Za-z]\d{3}")


def known_batches(conn: psycopg.Connection, model: str) -> list[str]:
    """该型号在库里实际出现过的批次（只取可检索的现行证据），用于追问选项。"""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select distinct c.batch
                  from chunks c
                  join documents d on d.id = c.document_id
                 where d.device_model = %s
                   and c.batch is not null
                   and coalesce(c.disabled, false) = false
                   and d.status = 'parsed'
                   and d.lifecycle not in ('disabled', 'archived')
                 order by c.batch
                """,
                (model,),
            )
            return [str(row[0]) for row in cur.fetchall() if row[0]]
    except Exception as exc:  # noqa: BLE001 - 追问是增强项，查询失败按不追问处理
        logger.warning("known_batches failed: %s", exc)
        return []


def build_batch_ask(
    conn: psycopg.Connection,
    question: str,
    structured: dict[str, Any],
    clarify: ClarifySelection | None,
) -> dict[str, Any] | None:
    """条件不足时的单轮追问（widget spec §4.3A）。

    只在一轮未带 clarify、宿主给了型号但没给批次、问题属于批次敏感类，
    且库里确实存在多个批次时触发；追问一轮为限。
    """
    if clarify is not None:
        return None
    if not structured.get("model") or structured.get("batch"):
        return None
    if not any(hint in question for hint in BATCH_SENSITIVE_HINTS):
        return None
    if _FAULT_CODE_PATTERN.search(question):
        return None
    batches = known_batches(conn, str(structured["model"]))
    if len(batches) < 2:
        return None
    options = [
        {"value": batch, "label": batch, "hint": "按该批次检索"}
        for batch in batches[:3]
    ]
    options.append(
        {
            "value": "unknown",
            "label": "不确定（并列展示）",
            "hint": "不选批次，系统并列展示不同批次的做法",
        }
    )
    return {
        "field": "batch",
        "title": ASK_BATCH_TITLE,
        "body": ASK_BATCH_BODY,
        "options": options,
    }


def run_turn(
    *,
    settings: Settings,
    conn: psycopg.Connection,
    llm: Any,
    embedder: Any,
    reranker: Any = None,
    question: str,
    context: HostContext | None = None,
    clarify: ClarifySelection | None = None,
) -> TurnOutcome:
    """跑一轮完整问答，返回结果对象（不写日志、不发 SSE）。"""
    started = time.perf_counter()
    structured = build_structured(question, context=context, clarify=clarify)
    outcome = TurnOutcome(question=question, structured=structured)

    # 0. 运行时配置覆盖（检索/安全词表）：保存后下一次问答即生效（D-012）；
    #    读取失败/无配置时 load_config 返回空，不影响主链路。
    runtime = load_config(conn, ("retrieval", "safety"))
    retrieval_overrides = runtime.get("retrieval", {})
    safety_overrides = runtime.get("safety", {})
    safety_words = tuple(safety_overrides.get("words") or settings.safety_word_list())

    # 1. 安全拦截（纪律 3）
    hit_word = match_safety_word(question, safety_words)
    if hit_word:
        outcome.safety_word = hit_word
        outcome.refused = True
        outcome.gate_reason = "safe"
        outcome.content = SAFETY_BODY
        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    # 1.5 条件不足的单轮追问（widget spec §4.3A）：先确认批次，再检索/作答
    ask = build_batch_ask(conn, question, structured, clarify)
    if ask:
        outcome.ask = ask
        outcome.gate_reason = "ask"
        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    # 2. 检索
    try:
        pipeline = RetrievalPipeline(
            settings=build_retrieval_config(settings, retrieval_overrides),
            embedder=embedder,
            reranker=reranker,
        )
        retrieval = pipeline.retrieve(
            conn,
            question=question,
            model=structured.get("model"),
            batch=structured.get("batch"),
            model_aliases=model_match_aliases(structured.get("model")),
        )
    except Exception as exc:  # noqa: BLE001 - 统一转成 error 结果
        logger.exception("retrieval failed: %s", exc)
        outcome.error = exc
        outcome.refused = True
        outcome.gate_reason = "error"
        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return outcome
    outcome.retrieval = retrieval
    outcome.filter_note = retrieval.filter_note

    # 3. 证据门（纪律 4）
    try:
        min_score = float(
            retrieval_overrides.get("evidenceThreshold", settings.retrieval_min_score)
        )
    except (TypeError, ValueError):
        min_score = settings.retrieval_min_score
    try:
        min_coverage = float(
            retrieval_overrides.get("evidenceMinCoverage", settings.evidence_min_coverage)
        )
    except (TypeError, ValueError):
        min_coverage = settings.evidence_min_coverage
    gate = evaluate_gate(
        retrieval.items,
        query_tokens=coverage_tokens(retrieval.query_tokens),
        min_hits=settings.evidence_min_hits,
        min_score=min_score,
        min_coverage=min_coverage,
    )
    outcome.gate = gate
    if not gate.passed:
        outcome.refused = True
        outcome.gate_reason = "gap"
        outcome.content = GAP_BODY
        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    # 4. 多版本冲突（纪律 7）
    outcome.conflict = detect_conflict(retrieval.items)

    # 5. 生成（temperature=0：同一证据要可复现；空内容加倍预算重试一次）
    messages_list = build_messages(question, retrieval, structured)
    budget = settings.llm_answer_max_tokens
    try:
        try:
            result = llm.complete(messages_list, max_tokens=budget, temperature=0.0)
        except LlmError as exc:
            if exc.code != "LLM_EMPTY_CONTENT":
                raise
            logger.warning("empty content, retry with larger budget: %d -> %d", budget, budget * 2)
            result = llm.complete(messages_list, max_tokens=budget * 2, temperature=0.0)
    except Exception as exc:  # noqa: BLE001
        logger.exception("pipeline llm failed: %s", exc)
        outcome.error = exc
        outcome.refused = True
        outcome.gate_reason = "error"
        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    # 6. 引用校验（纪律 1）
    parsed = parse_answer(result.text, allowed_items=retrieval.items)
    outcome.steps = parsed.steps
    outcome.citations = parsed.citations
    outcome.stripped = parsed.stripped
    if parsed.refused:
        outcome.refused = True
        outcome.gate_reason = "citation_stripped"
        outcome.content = parsed.steps[0].text if parsed.steps else ""
    else:
        outcome.content = "\n".join(step.text for step in parsed.steps)
    outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return outcome
