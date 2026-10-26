"""问答日志写入（`messages` 表）。

后台的「链路详情」要能回答"这条回答为什么是它"，所以这里落的不是一句话，
而是：结构化条件、命中的证据、最终引用、被剥离的引用、拒答原因、是否已解决。
S1 只落骨架模式需要的最小集合，字段留空不猜。
"""

from __future__ import annotations

from typing import Any

import psycopg


def _session_pk(session_id: str) -> int | None:
    raw = session_id.strip()
    if raw.startswith("ses-"):
        raw = raw[4:]
    return int(raw) if raw.isdigit() else None


def log_user_message(
    conn: psycopg.Connection,
    *,
    session_id: str,
    question: str,
    structured_query: dict[str, Any] | None = None,
) -> str | None:
    """记一条用户提问；返回消息 id（字符串）。"""
    sid = _session_pk(session_id)
    if sid is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into messages (session_id, role, content, structured_query, question_at)
            values (%s, 'user', %s, %s::jsonb, now())
            returning id
            """,
            (
                sid,
                question,
                psycopg.types.json.Jsonb(structured_query) if structured_query else None,
            ),
        )
        message_id = int(cur.fetchone()[0])
    conn.commit()
    return f"msg-{message_id}"


def log_assistant_message(
    conn: psycopg.Connection,
    *,
    session_id: str,
    content: str,
    refused: bool = False,
    gate_reason: str | None = None,
    evidence_ids: list[str] | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> str | None:
    """记一条助手回答；`gate_reason` 区分 gap / safe / skeleton / citation_stripped。"""
    sid = _session_pk(session_id)
    if sid is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into messages
                (session_id, role, content, refused, gate_reason, evidence_ids, citations)
            values (%s, 'assistant', %s, %s, %s, %s, %s::jsonb)
            returning id
            """,
            (
                sid,
                content,
                refused,
                gate_reason,
                evidence_ids or [],
                psycopg.types.json.Jsonb(citations or []),
            ),
        )
        message_id = int(cur.fetchone()[0])
    conn.commit()
    return f"msg-{message_id}"


def touch_resolved(conn: psycopg.Connection, *, session_id: str, resolved: bool) -> bool:
    """标记"已解决 / 未解决"（定位时长的终点，D9 口径）。S1 仅提供函数，接口在 S4。"""
    sid = _session_pk(session_id)
    if sid is None:
        return False
    with conn.cursor() as cur:
        cur.execute(
            """
            update messages
               set resolved = %s, resolved_at = now(), feedback = %s
             where id = (
               select id from messages
                where session_id = %s and role = 'assistant'
                order by created_at desc
                limit 1
             )
            returning id
            """,
            (resolved, "resolved" if resolved else "unresolved", sid),
        )
        updated = cur.fetchone() is not None
    conn.commit()
    return updated


# ---------- S4：反馈 / 证据详情 / 转人工 ----------
def _message_pk(message_id: str) -> int | None:
    raw = (message_id or "").strip()
    if raw.startswith("msg-"):
        raw = raw[4:]
    return int(raw) if raw.isdigit() else None


def get_message(
    conn: psycopg.Connection, *, session_id: str, message_id: str
) -> dict[str, Any] | None:
    """按会话取一条消息（反馈要带 messageId，必须校验它属于本会话）。"""
    sid = _session_pk(session_id)
    mid = _message_pk(message_id)
    if sid is None or mid is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "select id, role, content, evidence_ids, citations, feedback "
            "from messages where id = %s and session_id = %s",
            (mid, sid),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": int(row[0]),
        "role": row[1],
        "content": row[2],
        "evidence_ids": list(row[3] or []),
        "citations": row[4] or [],
        "feedback": row[5],
    }


def set_feedback(
    conn: psycopg.Connection,
    *,
    session_id: str,
    message_id: str,
    feedback: str,
    note: str | None = None,
) -> bool:
    """落反馈：resolved 同时写 resolved/resolved_at（定位时长终点），saved 只记收藏。"""
    sid = _session_pk(session_id)
    mid = _message_pk(message_id)
    if sid is None or mid is None:
        return False
    resolved = feedback == "resolved"
    with conn.cursor() as cur:
        cur.execute(
            """
            update messages
               set feedback = %s,
                   feedback_note = %s,
                   resolved = %s,
                   resolved_at = case when %s then now() else null end
             where id = %s and session_id = %s
            returning id
            """,
            (feedback, note, resolved, resolved, mid, sid),
        )
        updated = cur.fetchone() is not None
    conn.commit()
    return updated


def session_metrics(conn: psycopg.Connection, *, session_id: str) -> dict[str, int]:
    """会话级口径：轮次、累计引用条数、转人工次数（转人工弹层与后台复盘共用）。"""
    sid = _session_pk(session_id)
    if sid is None:
        return {"rounds": 0, "citedCount": 0, "handoffCount": 0}
    with conn.cursor() as cur:
        cur.execute(
            """
            select
              (select count(*) from messages
                where session_id = %s and role = 'user') as rounds,
              (select coalesce(sum(jsonb_array_length(citations)), 0)
                 from messages where session_id = %s and role = 'assistant') as cited,
              (select count(*) from handoffs where session_id = %s) as handoffs
            """,
            (sid, sid, sid),
        )
        row = cur.fetchone()
    return {"rounds": int(row[0]), "citedCount": int(row[1]), "handoffCount": int(row[2])}


def find_cited_evidence(
    conn: psycopg.Connection, *, session_id: str, evidence_id: str
) -> dict[str, Any] | None:
    """只在**本会话**实际引用过的证据里找：防止拿别的会话/全库切片来翻内容。

    返回切片正文与出处；没被本会话引用过就返回 None（路由层转 404）。
    """
    sid = _session_pk(session_id)
    if sid is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            select c.evidence_id, c.content, c.section_no, c.page_from, c.page_to,
                   c.meta_json, d.id, d.title, d.meta_json
              from chunks c
              join documents d on d.id = c.document_id
             where c.evidence_id = %s
               and c.disabled = false
               and exists (
                 select 1 from messages m
                  where m.session_id = %s
                    and m.role = 'assistant'
                    and %s = any(m.evidence_ids)
               )
            """,
            (evidence_id, sid, evidence_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "evidence_id": row[0],
        "content": row[1],
        "section_no": row[2],
        "page_from": row[3],
        "page_to": row[4],
        "chunk_meta": row[5] or {},
        "document_id": int(row[6]),
        "document_title": row[7],
        "document_meta": row[8] or {},
    }
