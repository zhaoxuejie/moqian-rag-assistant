"""工程师端路由：浮层配置、会话建立、引用原文、反馈、转人工（S1 起，S4 补齐）。

文案、示例问题、品牌色全部由 app 层注入（纪律 6），widget 代码里不写死行业词。
`/widget/*` 只认 `X-Widget-Session`（双链隔离），与 `/admin/*` 的 JWT 互不通用。
"""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, status

from .. import demo_gate, widget_service
from ..config import Settings, get_settings
from ..domain import DEFAULT_HANDOFF_TEXT
from ..messages import _session_pk
from ..runtime_config import load_config
from ..schemas import FeedbackRequest, HandoffRequest, SessionRequest
from ..sessions import create_session, get_session
from .deps import get_conn

router = APIRouter(prefix="/widget", tags=["widget"])

# 示例问题属行业词汇，放 app 层；S5 起由后台「系统配置」页覆盖。
# 配置只存文本，`tag`/`scene` 按下标继承这里的值——所以默认示例要与当前语料对齐，
# 否则后台换示例后会出现「半月维保」被标成「报警码」这种串标签。
DEFAULT_EXAMPLES = (
    {"q": "HY-E006 抱闸故障伴周期性异响，怎么处理？", "tag": "故障码", "scene": ""},
    {"q": "半月维保要检查哪些项目？", "tag": "维保", "scene": ""},
    {"q": "困人救援的基本步骤是什么？", "tag": "应急救援", "scene": ""},
)


def _unauthorized(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": message},
    )


def require_widget_session(
    conn: psycopg.Connection = Depends(get_conn),
    x_widget_session: str | None = Header(default=None, alias="X-Widget-Session"),
) -> dict:
    """会话校验：缺失 / 不存在 / 已失效一律 401（不泄露会话是否存在之外的细节）。"""
    if not x_widget_session:
        raise _unauthorized("UNAUTHORIZED", "缺少会话，请先建立会话")
    session = get_session(conn, x_widget_session)
    if session is None:
        raise _unauthorized("SESSION_INVALID", "会话不存在或已失效")
    return session


@router.get("/config")
def widget_config(
    conn=Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> dict:
    """浮层配置：`app_config` 的 widget/brand 组覆盖代码默认值（D-012）。"""
    overrides = load_config(conn, ("widget", "brand"))
    widget = overrides.get("widget", {})
    brand = overrides.get("brand", {})
    enabled, paused = demo_gate.demo_state(conn, settings)
    samples = widget.get("samples")
    examples = list(DEFAULT_EXAMPLES)
    if isinstance(samples, list) and samples:
        examples = []
        for index, text in enumerate(samples):
            text = str(text).strip()
            if not text:
                continue
            base = DEFAULT_EXAMPLES[index] if index < len(DEFAULT_EXAMPLES) else {}
            examples.append(
                {
                    "q": text,
                    "tag": base.get("tag", "示例"),
                    "scene": base.get("scene", ""),
                }
            )
    return {
        "welcome": widget.get("welcome")
        or "我是售后知识助手，可以按故障现象帮你查手册与历史工单，答案都带出处。",
        "examples": examples,
        "handoffText": widget.get("handoffText") or DEFAULT_HANDOFF_TEXT,
        "brandColor": widget.get("brandColor")
        or brand.get("primaryColor")
        or "#0f7a52",
        "features": {
            "evidence": True,
            "feedback": True,
            "handoff": True,
        },
        # 演示站信息：前端据此决定"没有入口码 / 被暂停"的提示与剩余轮次。
        # 只下会话级轮次（用户看得见的那一句），不下每日额度——那是运营参数。
        "demo": {
            "enabled": enabled,
            "paused": paused,
            "requiresCode": enabled and settings.demo_require_code,
            "sessionTurns": int(settings.demo_session_turns) if enabled else 0,
            "bookingUrl": settings.demo_booking_url,
            "pausedTitle": demo_gate.PAUSED_TITLE if paused else "",
            "pausedText": demo_gate.PAUSED_BODY if paused else "",
        },
    }


@router.post("/session")
def create_widget_session(
    payload: SessionRequest,
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> dict:
    demo_code: str | None = None
    if settings.demo_enabled:
        _, paused = demo_gate.demo_state(conn, settings)
        if paused:
            # 后台总开关关闭：即使带了入口码也不建会话（否则绕过"暂停"）
            raise _unauthorized("DEMO_PAUSED", demo_gate.PAUSED_BODY)
        code = (payload.demo_code or "").strip()
        if settings.demo_require_code:
            if not code:
                raise _unauthorized(
                    "DEMO_CODE_REQUIRED", "该演示需要入口码，请从官网案例页进入"
                )
            if demo_gate.verify_access_code(conn, code) is None:
                raise _unauthorized(
                    "DEMO_CODE_INVALID", "入口码无效或已过期，请从官网重新进入或预约演示"
                )
        demo_code = code or None
    return create_session(
        conn,
        requested_source=payload.identity.source,
        claimed_account=payload.identity.claimed_engineer_id,
        host_context=payload.host_context.to_dict() if payload.host_context else None,
        demo_code=demo_code,
    )


@router.get("/evidence/{evidence_id}")
def get_widget_evidence(
    evidence_id: str,
    session: dict = Depends(require_widget_session),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    evidence = widget_service.get_evidence(
        conn, session_id=f"ses-{session['id']}", evidence_id=evidence_id
    )
    if evidence is None:
        # 只在"本会话引用过"的证据里找；没有就 404，不泄露库内其它切片
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "EVIDENCE_NOT_FOUND", "message": "该引用不在本轮证据集中"},
        )
    return evidence


@router.post("/feedback")
def post_widget_feedback(
    payload: FeedbackRequest,
    session: dict = Depends(require_widget_session),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    if _session_pk(payload.session_id) != session["id"]:
        raise _unauthorized("SESSION_INVALID", "会话不匹配")
    recorded = widget_service.submit_feedback(
        conn,
        session_id=f"ses-{session['id']}",
        message_id=payload.message_id,
        feedback=payload.type,
        note=payload.note,
    )
    if not recorded:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "MESSAGE_NOT_FOUND", "message": "消息不存在或不属于本会话"},
        )
    return {"ok": True}


@router.post("/handoff")
def post_widget_handoff(
    payload: HandoffRequest,
    session: dict = Depends(require_widget_session),
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> dict:
    if _session_pk(payload.session_id) != session["id"]:
        raise _unauthorized("SESSION_INVALID", "会话不匹配")
    return widget_service.build_handoff(
        conn,
        session=session,
        message_id=payload.message_id,
        settings=settings,
    )
