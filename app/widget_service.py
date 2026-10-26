"""S4 Widget 支撑：引用原文、反馈、转人工。

三件事都挂在**会话**上，先校验会话再动数据：
- 证据详情：只允许取"本会话实际引用过"的切片（不泄露库内其它内容）；
- 反馈：messageId 必须属于本会话，否则 404；`resolved` 同时写定位时长终点；
- 转人工：P0 只返回热线与上下文快照，并把这次转人工落 `handoffs` 表。

行业词汇（热线、值班时间）由配置/`domain` 注入，不写死在本模块。
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from . import messages, settlements
from .config import Settings
from .domain import DEFAULT_HANDOFF_TEXT
from .serializers import page_label

logger = logging.getLogger(__name__)

# 资料分类 → 契约 Citation.kind（行业词汇映射，放 app 层）
_CATEGORY_TO_KIND = {
    "manual": "manual",
    "ticket": "ticket",
    "faq": "faq",
    "drawing": "drawing",
    "excel": "excel",
    "video": "video",
}


def _kind_of(document_meta: dict[str, Any], chunk_meta: dict[str, Any]) -> str:
    category = document_meta.get("category") or chunk_meta.get("category")
    return _CATEGORY_TO_KIND.get(str(category), "manual")


def _loc(
    section_no: str | None,
    page_from: int | None,
    page_to: int | None,
    stored_loc: str | None = None,
) -> str:
    """出处优先级：落库的 meta.loc（章节标题 / 工作表行号）> 章节号 > 页码。

    手工/Word/Markdown 的章节常常没有编号（"一、品牌标识"），只拼 section_no 会得到空
    出处；表格行的 loc 是「工作表 第N行」，也不该被改写成「§序号」。
    """
    if stored_loc:
        return str(stored_loc)
    parts: list[str] = []
    if section_no:
        parts.append(f"§{section_no}")
    page = page_label(page_from, page_to) if page_from else None
    if page:
        parts.append(page)
    return " · ".join(parts)


def get_evidence(
    conn: psycopg.Connection, *, session_id: str, evidence_id: str
) -> dict[str, Any] | None:
    raw = messages.find_cited_evidence(
        conn, session_id=session_id, evidence_id=evidence_id
    )
    if raw is None:
        return None
    section_no = raw["section_no"]
    title = f"《{raw['document_title']}》" + (f"§{section_no}" if section_no else "")
    chunk_meta = raw["chunk_meta"] or {}
    return {
        "evidenceId": raw["evidence_id"],
        "kind": _kind_of(raw["document_meta"], chunk_meta),
        "title": title,
        "loc": _loc(
            section_no, raw["page_from"], raw["page_to"], chunk_meta.get("loc")
        ),
        "batch": chunk_meta.get("batch"),
        "text": raw["content"],
        "documentId": f"doc-{raw['document_id']}",
        "pageFrom": raw["page_from"],
        "pageTo": raw["page_to"],
        "sectionNo": section_no,
        "meta": {
            "loc": chunk_meta.get("loc"),
            "heading": chunk_meta.get("heading"),
        },
    }


def submit_feedback(
    conn: psycopg.Connection,
    *,
    session_id: str,
    message_id: str,
    feedback: str,
    note: str | None = None,
) -> bool:
    recorded = messages.set_feedback(
        conn,
        session_id=session_id,
        message_id=message_id,
        feedback=feedback,
        note=note,
    )
    # 收藏 = 沉淀候选的生产端（FR-K5）：先生成待确认候选，仍由管理员确认才入库。
    # 候选写入失败不能把「收藏已记录」变成 500——收藏本身已经落库，管理端可复盘。
    if recorded and feedback == "saved":
        try:
            settlements.create_from_favorite(
                conn, session_id=session_id, message_id=message_id
            )
        except Exception:  # noqa: BLE001 - 候选生成是附属动作，不阻断反馈
            logger.warning(
                "favorite settlement creation failed (session=%s message=%s)",
                session_id,
                message_id,
                exc_info=True,
            )
    return recorded


def build_handoff(
    conn: psycopg.Connection,
    *,
    session: dict[str, Any],
    message_id: str | None,
    settings: Settings,
) -> dict[str, Any]:
    """返回热线 + 与当前会话真实一致的上下文快照，并记录一次转人工。"""
    session_pk = int(session["id"])
    session_id = f"ses-{session_pk}"
    host_context = session.get("host_context") or {}
    metrics = messages.session_metrics(conn, session_id=session_id)

    message_pk = None
    if message_id:
        message = messages.get_message(conn, session_id=session_id, message_id=message_id)
        message_pk = int(message["id"]) if message else None

    context = {
        "deviceModel": host_context.get("deviceModel"),
        "batch": host_context.get("batch"),
        "citedCount": metrics["citedCount"],
        "handoffCount": metrics["handoffCount"] + 1,
        "rounds": metrics["rounds"],
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into handoffs (session_id, message_id, context)
            values (%s, %s, %s::jsonb)
            """,
            (session_pk, message_pk, psycopg.types.json.Jsonb(context)),
        )
    conn.commit()

    return {
        "hotline": settings.handoff_hotline,
        "hours": settings.handoff_hours,
        "ticketNo": host_context.get("ticketNo"),
        "context": context,
        "text": DEFAULT_HANDOFF_TEXT,
    }
