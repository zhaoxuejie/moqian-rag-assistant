"""`/chat` 的服务端编排：把 `rag_runner` 的一轮结果翻译成 SSE 事件并落日志。

事件顺序严格按 widget spec §10：
    structured →（可选 conflict）→ steps → citations → done
异常路径 `gap` / `safe` / `error` **不发半成品答案**。

计算在 `app.rag_runner.run_turn`（与评测 runner 复用同一套代码，ADR9）；
这里只负责：安全/骨架模式分支、SSE 帧、messages 落库、日志。

两种模式：
- `CHAT_MODE=pipeline`（默认）：真检索 + 证据门 + 引用校验；
- `CHAT_MODE=skeleton`：只验证链路连通，输出带「骨架模式」标识、不带引用（decisions §7）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import psycopg

from . import messages
from .config import Settings
from .domain import (
    GAP_BODY,
    GAP_TITLE,
    SAFETY_BODY,
    SAFETY_TITLE,
    SKELETON_NOTICE,
    SKELETON_SYSTEM_PROMPT,
)
from .rag_runner import build_structured, match_safety_word, run_turn
from .schemas import ChatRequest
from .sse import sse_event

logger = logging.getLogger(__name__)

_CITATION_STRIPPED_BODY = (
    "模型的回答没有可核验的引用，已按规则剥离，本次不作答。\n本次查询已记入知识缺口清单。"
)


def stream_chat(
    *,
    settings: Settings,
    conn: psycopg.Connection,
    llm: Any,
    embedder: Any,
    reranker: Any,
    request: ChatRequest,
) -> Iterator[str]:
    """生成 SSE 帧序列。同步生成器，交给 StreamingResponse。"""
    started = time.perf_counter()
    messages.log_user_message(
        conn,
        session_id=request.session_id,
        question=request.question,
        structured_query=build_structured(
            request.question, context=request.context, clarify=request.clarify
        ),
    )

    if settings.chat_mode == "skeleton":
        structured = build_structured(
            request.question, context=request.context, clarify=request.clarify
        )
        yield sse_event("structured", {"structured": structured, "filterNote": None})
        hit_word = match_safety_word(request.question, settings.safety_word_list())
        if hit_word:
            yield from _stream_safety(conn=conn, request=request, word=hit_word)
            return
        yield from _stream_skeleton(settings=settings, conn=conn, llm=llm, request=request)
        return

    outcome = run_turn(
        settings=settings,
        conn=conn,
        llm=llm,
        embedder=embedder,
        reranker=reranker,
        question=request.question,
        context=request.context,
        clarify=request.clarify,
    )
    yield sse_event(
        "structured", {"structured": outcome.structured, "filterNote": outcome.filter_note}
    )

    if outcome.ask:
        # 单轮追问：不生成答案、不写 assistant 消息；用户选定后带 clarify 再请求一次。
        yield sse_event("ask", {"ask": outcome.ask})
        return

    if outcome.gate_reason == "safe":
        yield from _stream_safety(conn=conn, request=request, word=outcome.safety_word)
        return

    if outcome.gate_reason == "error":
        yield _error_event(outcome.error or RuntimeError("chat failed"))
        messages.log_assistant_message(
            conn, session_id=request.session_id, content="", refused=True, gate_reason="error"
        )
        return

    if outcome.gate_reason in {"gap", "citation_stripped"}:
        body = _CITATION_STRIPPED_BODY if outcome.gate_reason == "citation_stripped" else GAP_BODY
        message_id = messages.log_assistant_message(
            conn,
            session_id=request.session_id,
            content=outcome.content or body,
            refused=True,
            gate_reason=outcome.gate_reason,
            evidence_ids=outcome.retrieval.evidence_ids() if outcome.retrieval else None,
        )
        gate = outcome.gate
        yield sse_event(
            "gap",
            {
                "gap": {
                    "title": GAP_TITLE,
                    "body": body,
                    "messageId": message_id,
                    "missingFields": (gate.missing_terms[:5] if gate else []),
                }
            },
        )
        return

    if outcome.conflict:
        yield sse_event("conflict", outcome.conflict)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    retrieval = outcome.retrieval
    logger.info(
        "chat pipeline: dense=%d bm25=%d top=%d steps=%d citations=%d stripped=%s elapsed_ms=%d",
        retrieval.dense_count if retrieval else 0,
        retrieval.bm25_count if retrieval else 0,
        retrieval.fused_count if retrieval else 0,
        len(outcome.steps),
        len(outcome.citations),
        outcome.stripped,
        elapsed_ms,
    )
    message_id = messages.log_assistant_message(
        conn,
        session_id=request.session_id,
        content=outcome.content,
        refused=False,
        gate_reason=None,
        evidence_ids=[item.evidence_id for item in outcome.citations],
        citations=[item.to_contract() for item in outcome.citations],
    )
    for step in outcome.steps:
        yield sse_event(
            "steps",
            {
                "index": step.index,
                "text": step.text,
                "citations": step.citations,
                "done": step.index == len(outcome.steps),
            },
        )
    yield sse_event(
        "citations", {"items": [item.to_contract() for item in outcome.citations]}
    )
    yield sse_event(
        "done",
        {
            "messageId": message_id,
            "citationsCount": len(outcome.citations),
            "refused": False,
            "elapsedMs": elapsed_ms,
        },
    )


def _stream_safety(
    *, conn: psycopg.Connection, request: ChatRequest, word: str | None
) -> Iterator[str]:
    logger.info("chat blocked by safety word: %s", word)
    message_id = messages.log_assistant_message(
        conn,
        session_id=request.session_id,
        content=SAFETY_BODY,
        refused=True,
        gate_reason="safe",
    )
    yield sse_event(
        "safe",
        {
            "safe": {
                "title": SAFETY_TITLE,
                "body": SAFETY_BODY,
                "citations": [],
                "messageId": message_id,
            }
        },
    )


# ---------- 骨架模式（只验链路，不是正式答案） ----------
def _stream_skeleton(
    *, settings: Settings, conn: psycopg.Connection, llm: Any, request: ChatRequest
) -> Iterator[str]:
    messages_list = [
        {"role": "system", "content": SKELETON_SYSTEM_PROMPT},
        {"role": "user", "content": request.question},
    ]
    collected: list[str] = []
    index = 0
    started = time.perf_counter()
    try:
        iterator, stats = llm.stream_with_stats(messages_list)
        for chunk in iterator:
            collected.append(chunk)
            index += 1
            yield sse_event("steps", {"index": index, "text": chunk, "citations": []})
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        content = "".join(collected).strip()
        message_id = messages.log_assistant_message(
            conn,
            session_id=request.session_id,
            content=content,
            refused=False,
            gate_reason="skeleton",
        )
        logger.info(
            "chat skeleton ok: chars=%d chunks=%d first_token_ms=%s elapsed_ms=%d",
            stats.chars,
            stats.chunks,
            stats.first_token_ms,
            elapsed_ms,
        )
        yield sse_event(
            "done",
            {
                "messageId": message_id,
                "citationsCount": 0,
                "refused": False,
                "elapsedMs": elapsed_ms,
                "skeleton": True,
                "notice": SKELETON_NOTICE,
            },
        )
    except Exception as exc:  # noqa: BLE001 - 失败必须转成 SSE error，不能断连
        logger.exception("chat skeleton failed: %s", exc)
        yield _error_event(exc)
        messages.log_assistant_message(
            conn,
            session_id=request.session_id,
            content="",
            refused=True,
            gate_reason="error",
        )


def _error_event(exc: Exception) -> str:
    as_event = getattr(exc, "as_event", None)
    payload = (
        as_event()
        if callable(as_event)
        else {"code": "CHAT_FAILED", "message": "服务暂时不可用，请重试", "retryable": True}
    )
    return sse_event("error", {"error": payload})
