"""问答主链路 `/chat`：SSE 流式。

契约要点（widget spec §10）：
- 请求必须带 `X-Widget-Session`（会话在 `/widget/session` 建立）；
- 响应 `text/event-stream`，禁缓冲（Nginx 要 `X-Accel-Buffering: no`）；
- 事件顺序与错误分支由 `app.chat_service.stream_chat` 保证。

S1 用 `CHAT_MODE=skeleton` 验证"链路能吐字"；S3 起默认 `pipeline`：
向量 + BM25 → RRF → 证据门 → 带引用回答 → 引用校验。
"""

from __future__ import annotations

import logging

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from .. import demo_gate
from ..chat_service import stream_chat
from ..config import Settings, get_settings
from ..schemas import ChatRequest, HostContext
from ..sessions import get_session
from ..sse import sse_event
from .deps import get_conn, get_embedder, get_llm, get_reranker

router = APIRouter(tags=["chat"])

logger = logging.getLogger(__name__)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _quota_stream(decision: demo_gate.QuotaDecision, settings: Settings):
    """额度用完：只发一帧 `refused`，不写 messages、不调模型。"""
    if decision.reason == "session":
        title = "本次演示对话已结束"
        body = (
            "演示环境每次对话有轮次上限。重新打开助手会开启新的对话；"
            "如果需要按你的场景完整评估，可以预约 1:1 演示。"
        )
    else:
        title = "今日演示额度已用完"
        body = "演示环境有每日额度，以免影响其他访客。可以预约 1:1 演示，我们按你的场景走一遍。"
    payload = {
        "refused": {
            "kind": "quota",
            "reason": decision.reason,
            "title": title,
            "body": body,
            "bookingUrl": settings.demo_booking_url,
            "retryable": False,
        }
    }
    yield sse_event("refused", payload)


def _paused_stream(settings: Settings):
    """后台「演示总开关」关闭：已建立的会话也不再作答（不写 messages、不调模型）。

    与 quota 区分开：这是「运营主动暂停」，不是额度用完，所以给的是"从官网入口进入 /
    预约演示"，而不是"明天再来"。
    """
    payload = {
        "refused": {
            "kind": "paused",
            "reason": None,
            "title": demo_gate.PAUSED_TITLE,
            "body": demo_gate.PAUSED_BODY,
            "bookingUrl": settings.demo_booking_url,
            "retryable": False,
        }
    }
    yield sse_event("refused", payload)


@router.post("/chat")
def post_chat(
    request: Request,
    payload: ChatRequest,
    x_widget_session: str | None = Header(default=None, alias="X-Widget-Session"),
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
    llm=Depends(get_llm),
    embedder=Depends(get_embedder),
    reranker=Depends(get_reranker),
) -> StreamingResponse:
    if not x_widget_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "缺少会话，请先建立会话"},
        )
    session = get_session(conn, x_widget_session)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "SESSION_INVALID", "message": "会话不存在或已失效"},
        )

    # 演示配额：在调用 LLM 之前预占（关掉演示模式时直接放行）
    if settings.demo_enabled:
        _, paused = demo_gate.demo_state(conn, settings)
        if paused:
            logger.info("chat blocked: demo paused session=%s", session["id"])
            return StreamingResponse(
                _paused_stream(settings),
                media_type="text/event-stream",
                headers=_SSE_HEADERS,
            )
        decision = demo_gate.reserve(
            conn,
            session_pk=session["id"],
            code=session.get("demo_code"),
            ip=demo_gate.client_ip(request, settings),
            settings=settings,
        )
        if not decision.allowed:
            logger.info(
                "chat quota blocked: reason=%s session=%s code=%s",
                decision.reason,
                session["id"],
                session.get("demo_code"),
            )
            return StreamingResponse(
                _quota_stream(decision, settings),
                media_type="text/event-stream",
                headers=_SSE_HEADERS,
            )

    # 请求没带 context 时沿用会话建立时记录的宿主上下文（同一份声明，不是推测补全）
    if payload.context is None and session["host_context"]:
        payload = payload.model_copy(
            update={"context": HostContext.model_validate(session["host_context"])}
        )

    return StreamingResponse(
        stream_chat(
            settings=settings,
            conn=conn,
            llm=llm,
            embedder=embedder,
            reranker=reranker,
            request=payload,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
