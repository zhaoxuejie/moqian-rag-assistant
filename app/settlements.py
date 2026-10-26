"""知识沉淀候选的生产端（PRD FR-K5 双进水管 / 纪律 8）。

两条通道都只产出 `settlements` 里的**待确认候选**，绝不直接入库：

- 收藏：工程师在问答里点「收藏」→ 该轮回答成为 `channel='favorite'` 候选；
- 工单：工单完成后由管理员在问答日志点「一键沉淀」→ 该工单的已解决轮次成为
  `channel='ticket'` 候选（当前产品没有独立工单表，`sessions.host_context.ticketNo`
  是唯一锚点；P0 不写回宿主工单系统）。

审核侧（确认/驳回、生成切片、留痕）在 `app/routers/admin_settlements.py`，本模块不重复。

幂等键 `origin_ref`（迁移 0007 的部分唯一索引兜底）：
- 收藏 = `msg-<消息 id>`：一条回答最多一条收藏候选；
- 工单 = `ticket:<工单号>`：一张工单最多一条待确认候选。
`rejected` 不占键：驳回后允许重新提交候选，历史保留。

与 `messages.feedback` 的关系：收藏候选是收藏记录的「审核影子」——生成后与反馈列
解耦，工程师之后改点已解决/没解决、或管理员驳回，都不回写收藏记录。
"""

from __future__ import annotations

import logging
import re
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .sessions import source_label

logger = logging.getLogger(__name__)

# 回答正文里的证据标记（[M37004] / [T12]）；入库正文不应携带内部 ID。
_CITATION_MARK = re.compile(r"\s*\[[A-Za-z]\d+\]")
_TITLE_LIMIT = 40


class SettlementError(Exception):
    """生产端的业务前置不满足；路由层负责映射成 HTTP 状态码。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def strip_citation_marks(text: str) -> str:
    return _CITATION_MARK.sub("", text or "").strip()


def _summary(text: str, limit: int = _TITLE_LIMIT) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def _id(raw: str | None, prefix: str) -> int | None:
    value = (raw or "").strip()
    if value.startswith(prefix):
        value = value[len(prefix) :]
    return int(value) if value.isdigit() else None


def _context(rows: list[tuple[str, Any]]) -> list[dict[str, str]]:
    """上下文键值对：空值不写（审核页不展示「型号：—」这种噪声）。"""
    items: list[dict[str, str]] = []
    for key, value in rows:
        text = str(value).strip() if value is not None else ""
        if text:
            items.append({"k": key, "v": text})
    return items


def _answer_kind(row: dict[str, Any]) -> str:
    if row.get("gate_reason") == "safe":
        return "安全拦截（未生成操作步骤）"
    if row.get("refused") or row.get("gate_reason") == "gap":
        return "证据不足拒答"
    return "正常作答"


def _insert_candidate(
    conn: psycopg.Connection,
    *,
    channel: str,
    origin_ref: str,
    title: str,
    source: str | None,
    model: str | None,
    batch: str | None,
    version: str | None,
    submitted_by: str | None,
    body: str,
    loc: str | None,
    context: list[dict[str, str]],
) -> tuple[int, bool]:
    """插入候选并返回 (id, created)；幂等键冲突时返回既有条目。"""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            insert into settlements
                (channel, status, title, source, model, batch, version, submitted_by,
                 body, loc, context, origin_ref)
            values (%s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            on conflict (channel, origin_ref)
              where origin_ref is not null and status <> 'rejected'
              do nothing
            returning id
            """,
            (
                channel,
                title,
                source,
                model,
                batch,
                version,
                submitted_by,
                body,
                loc,
                Jsonb(context),
                origin_ref,
            ),
        )
        inserted = cur.fetchone()
        created = inserted is not None
        if inserted is None:
            cur.execute(
                "select id from settlements where channel = %s and origin_ref = %s "
                "and status <> 'rejected' order by id desc limit 1",
                (channel, origin_ref),
            )
            existing = cur.fetchone()
            if existing is None:  # 理论上不可达：唯一索引保证一定有既有行
                raise SettlementError(409, "CONFLICT", "候选写入冲突，请重试")
            settlement_id = int(existing["id"])
        else:
            settlement_id = int(inserted["id"])
    conn.commit()
    return settlement_id, created


# ---------------------------------------------------------------- 收藏 → 候选


def create_from_favorite(
    conn: psycopg.Connection, *, session_id: str, message_id: str
) -> int | None:
    """收藏一条回答时生成候选；返回沉淀 id（已存在则返回既有 id）。

    跳过管理自测会话（`channel='admin_preview'`）：自测不进任何统计，也不该污染审核队列。
    """
    sid = _id(session_id, "ses-")
    mid = _id(message_id, "msg-")
    if sid is None or mid is None:
        return None

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select m.content, m.refused, m.gate_reason, m.citations,
                   q.content as question, q.structured_query,
                   s.channel, s.host_context, s.auth_source,
                   u.name as engineer_name, u.account as engineer_account
              from messages m
              join sessions s on s.id = m.session_id
              left join users u on u.id = s.engineer_id
              left join lateral (
                    select content, structured_query
                      from messages q
                     where q.session_id = m.session_id
                       and q.role = 'user'
                       and q.id < m.id
                     order by q.id desc
                     limit 1
                   ) q on true
             where m.id = %s and m.session_id = %s and m.role = 'assistant'
            """,
            (mid, sid),
        )
        row = cur.fetchone()

    if row is None:
        return None
    if row["channel"] == "admin_preview":
        logger.info("skip favorite settlement for admin_preview session ses-%s", sid)
        return None

    body = strip_citation_marks(row["content"])
    if not body:
        logger.info("skip favorite settlement: empty answer after stripping msg-%s", mid)
        return None

    host = row.get("host_context") or {}
    structured = row.get("structured_query") or {}
    model = host.get("deviceModel") or structured.get("model")
    batch = host.get("batch") or structured.get("batch")
    question = (row.get("question") or "").strip()
    account = row.get("engineer_account")
    identity = source_label(row.get("auth_source"))
    if account:
        identity = f"{identity} · {account}"
    submitted_by = (row.get("engineer_name") or "").strip() or "未识别"

    context = _context(
        [
            ("问题", question),
            ("设备型号", model),
            ("批次", batch),
            ("工单号", host.get("ticketNo")),
            ("身份来源", identity),
            ("回答类型", _answer_kind(row)),
            ("引用条数", len(row.get("citations") or [])),
        ]
    )
    title = _summary(question) or f"会话 ses-{sid} 收藏的回答"

    return _insert_candidate(
        conn,
        channel="favorite",
        origin_ref=f"msg-{mid}",
        title=title,
        source=f"会话 ses-{sid} · msg-{mid}",
        model=model,
        batch=batch,
        version=None,
        submitted_by=submitted_by,
        body=body,
        loc=None,
        context=context,
    )[0]


# ---------------------------------------------------------------- 工单 → 候选


def create_from_ticket(
    conn: psycopg.Connection,
    *,
    session_id: str,
    triggered_by: str,
    note: str | None = None,
) -> tuple[int, bool]:
    """工单完成 → 候选；返回 (沉淀 id, 是否新建)。

    前置：会话存在、带 ticketNo、该工单至少有一轮「已解决」。
    """
    sid = _id(session_id, "ses-")
    if sid is None:
        raise SettlementError(404, "NOT_FOUND", "会话不存在")

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select id, channel, host_context from sessions where id = %s", (sid,)
        )
        session = cur.fetchone()
    if session is None:
        raise SettlementError(404, "NOT_FOUND", "会话不存在")

    ticket = (session.get("host_context") or {}).get("ticketNo")
    ticket = str(ticket).strip() if ticket else ""
    if not ticket:
        raise SettlementError(
            422,
            "VALIDATION_ERROR",
            "该会话没有工单号：工单通道仅适用于带工单上下文的会话",
        )

    origin_ref = f"ticket:{ticket}"
    with conn.cursor(row_factory=dict_row) as cur:
        # 先查幂等键：已生成过的候选（待确认/已入库）直接返回，避免重复点击报错。
        cur.execute(
            "select id from settlements where channel = 'ticket' and origin_ref = %s "
            "and status <> 'rejected' order by id desc limit 1",
            (origin_ref,),
        )
        existing = cur.fetchone()
        if existing is not None:
            return int(existing["id"]), False

        cur.execute(
            """
            select q.content as question, a.content as answer,
                   a.evidence_ids, a.citations,
                   q.structured_query as structured_query,
                   s.id as session_id, s.host_context,
                   u.name as engineer_name
              from messages a
              join messages q
                on q.session_id = a.session_id
               and q.role = 'user'
               and q.id = (
                     select max(x.id)
                       from messages x
                      where x.session_id = a.session_id
                        and x.role = 'user'
                        and x.id < a.id
                   )
              join sessions s on s.id = a.session_id
              left join users u on u.id = s.engineer_id
             where coalesce(s.host_context ->> 'ticketNo', '') = %s
               and s.channel <> 'admin_preview'
               and (a.resolved is true or a.feedback = 'resolved')
             order by q.created_at asc, q.id asc
            """,
            (ticket,),
        )
        rounds = cur.fetchall()

    if not rounds:
        raise SettlementError(
            409,
            "CONFLICT",
            "该工单还没有已解决的轮次，不能沉淀：请工程师在问答里标记「已解决」后再试",
        )

    first = rounds[0]
    host = first.get("host_context") or {}
    structured = first.get("structured_query") or {}
    model = host.get("deviceModel") or structured.get("model")
    batch = host.get("batch") or structured.get("batch")

    blocks: list[str] = []
    for index, row in enumerate(rounds, start=1):
        question = (row.get("question") or "").strip()
        answer = strip_citation_marks(row.get("answer") or "")
        prefix = f"第 {index} 轮\n" if len(rounds) > 1 else ""
        blocks.append(f"{prefix}【故障现象】{question}\n【处理方式】{answer}")
    body = "\n\n".join(blocks).strip()
    if not body:
        raise SettlementError(409, "CONFLICT", "该工单的已解决轮次没有可沉淀的正文")

    session_ids: list[str] = []
    for row in rounds:
        label = f"ses-{int(row['session_id'])}"
        if label not in session_ids:
            session_ids.append(label)
    submitted_by = (first.get("engineer_name") or "").strip() or "未识别"

    context = _context(
        [
            ("工单号", ticket),
            ("设备型号", model),
            ("批次", batch),
            ("已解决轮次", len(rounds)),
            ("涉及会话", "、".join(session_ids)),
            ("触发人", triggered_by),
            ("触发备注", note),
        ]
    )
    title = f"工单 {ticket} · {_summary(first.get('question') or '') or '处置记录'}"

    return _insert_candidate(
        conn,
        channel="ticket",
        origin_ref=origin_ref,
        title=title,
        source=ticket,
        model=model,
        batch=batch,
        version=None,
        submitted_by=submitted_by,
        body=body,
        loc=f"{ticket} · 处置记录",
        context=context,
    )
