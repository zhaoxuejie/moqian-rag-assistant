"""后台「问答日志」与「知识缺口」接口（P1 两页）。

口径（见 contracts/openapi.yaml）：
- **一轮 = 一条 user 消息 + 同一会话中紧随其后的 assistant 消息**（按 id 相邻，
  中间不能夹着下一条 user 消息）。outcome 由 `refused/gate_reason` 推导，
  不根据回答文本猜：gap/safe/error/answered。
- `withStats` 的统计**统一排除** `channel = 'admin_preview'`（管理自测不进指标，PRD §5.7）；
  列表本身仍返回 admin_preview 记录并带 `channel`，便于对账。
- 知识缺口是「证据不足拒答」的**派生视图**：缺口内容从 `messages` 聚合，
  `migrations/0004` 的 gaps 表只保存人工状态（status/covered_by/note）与聚合快照。
  每次读取先做一次幂等回填/校准，因此首次 GET 即完成历史回填。
- `messages` 表没有落"被剥离的具体证据 ID"：对 `gate_reason='citation_stripped'`
  的轮次只给出"引用被整条剥离"的聚合说明项，不编造 evidenceId。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import Field

from ..schemas import CamelModel
from ..serializers import page_label
from .deps import get_conn, require_governance_admin

router = APIRouter(
    prefix="/admin",
    tags=["admin-governance"],
    dependencies=[Depends(require_governance_admin)],
)

IdentityFilter = Literal[
    "gateway_sso", "host_injected", "password_login", "anonymous", "unidentified"
]
Outcome = Literal["answered", "gap", "safe", "error"]
ResolvedFilter = Literal["resolved", "unresolved", "none"]
GapStatus = Literal["open", "covered", "ignored"]

# messages 只存了"整条剥离"的结论，没有逐条证据 ID；这里用一条说明项表达"发生过剥离"，
# 而不是伪造 evidenceId（纪律 1 的审计线索不能被编造）。
_STRIPPED_REASON = "回答中的引用未通过引用校验，已整条剥离（具体证据 ID 未落库）"

_STRIPPED = [{"reason": _STRIPPED_REASON}]
_STRIPPED_ROUND = [_STRIPPED_REASON]

_KIND_BY_SOURCE = {
    "pdf": "manual",
    "word": "manual",
    "excel": "excel",
    "ticket": "ticket",
    "drawing": "drawing",
    "video": "video",
    "audio": "faq",
}

# 一轮问答的规范视图：user 消息 + 相邻 assistant 消息 + 会话身份/渠道/转人工。
_ROUNDS_CTE = """
with round_base as (
    select
        u.id as question_id,
        u.session_id,
        u.content as question,
        u.structured_query,
        u.question_at,
        u.created_at as asked_at,
        a.id as answer_id,
        a.content as answer,
        a.refused,
        a.gate_reason,
        a.evidence_ids,
        a.citations,
        a.resolved,
        a.resolved_at,
        a.feedback,
        a.created_at as answered_at,
        s.auth_source,
        s.claimed_engineer_id,
        s.engineer_id,
        s.channel,
        s.host_context,
        gu.name as engineer_name,
        gu.account as engineer_account,
        exists (select 1 from handoffs h where h.session_id = s.id) as handoff
      from messages u
      join sessions s on s.id = u.session_id
      left join users gu on gu.id = s.engineer_id
      left join lateral (
            select m.id, m.content, m.refused, m.gate_reason, m.evidence_ids,
                   m.citations, m.resolved, m.resolved_at, m.feedback, m.created_at
              from messages m
             where m.session_id = u.session_id
               and m.role = 'assistant'
               and m.id > u.id
               and not exists (
                     select 1 from messages x
                      where x.session_id = u.session_id
                        and x.role = 'user'
                        and x.id > u.id
                        and x.id < m.id
                   )
             order by m.id
             limit 1
           ) a on true
     where u.role = 'user'
),
rounds as (
    select rb.*,
           case
               when rb.answer_id is null then 'error'
               when rb.gate_reason in ('gap', 'citation_stripped') then 'gap'
               when rb.gate_reason = 'safe' then 'safe'
               when rb.gate_reason = 'error' then 'error'
               when rb.refused then 'gap'
               else 'answered'
           end as outcome,
           case
               when rb.resolved is true or rb.feedback = 'resolved' then 'resolved'
               when rb.resolved is false or rb.feedback = 'unresolved' then 'unresolved'
               else null
           end as resolved_state
      from round_base rb
)
"""

# 缺口聚合源：每条「证据不足拒答」的 assistant 消息配它前面最近的一条 user 提问。
_GAP_TURNS_SQL = """
select q.id as question_id,
       q.content as question,
       q.structured_query ->> 'model' as model,
       q.created_at as asked_at
  from messages a
  join messages q
    on q.session_id = a.session_id
   and q.role = 'user'
   and q.id = (
         select max(m.id)
           from messages m
          where m.session_id = a.session_id
            and m.role = 'user'
            and m.id < a.id
       )
 where a.role = 'assistant'
   and a.refused = true
   and a.gate_reason = 'gap'
 order by q.id
"""

_GAP_UPSERT = """
insert into gaps
    (symptom_key, symptom, model, count, first_at, last_at, related_messages)
values (%s, %s, %s, %s, %s, %s, %s)
on conflict (symptom_key) do update
   set symptom = excluded.symptom,
       model = coalesce(excluded.model, gaps.model),
       count = excluded.count,
       first_at = excluded.first_at,
       last_at = excluded.last_at,
       related_messages = excluded.related_messages,
       updated_at = now()
"""


class GapPatchRequest(CamelModel):
    """`PATCH /admin/gaps/{gapId}`：状态必填，覆盖来源/备注可选（只改显式传入的字段）。"""

    status: GapStatus
    covered_by: str | None = Field(default=None, alias="coveredBy", max_length=200)
    note: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------- 列表与详情


@router.get("/messages")
def list_messages(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    outcome: Outcome | None = Query(default=None),
    resolved: ResolvedFilter | None = Query(default=None),
    identity: IdentityFilter | None = Query(default=None),
    with_stats: bool = Query(default=False, alias="withStats"),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """分页问答日志；每行是一轮（问题 + 相邻回答）而不是一条原始消息。"""
    clause, params = _round_filters(
        outcome=outcome,
        resolved=resolved,
        identity=identity,
        date_from=date_from,
        date_to=date_to,
    )

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_ROUNDS_CTE + f" select count(*) as total from rounds where {clause}", params)
        total = int(cur.fetchone()["total"])
        cur.execute(
            _ROUNDS_CTE
            + f" select * from rounds where {clause}"
            + " order by asked_at desc, question_id desc limit %s offset %s",
            [*params, page_size, (page - 1) * page_size],
        )
        rows = cur.fetchall()

    hits = _hits_by_round(conn, rows) if rows else {}
    items = [_round_to_contract(row, hits.get(row["question_id"], [])) for row in rows]

    body: dict[str, Any] = {
        "items": items,
        "pagination": {"page": page, "pageSize": page_size, "total": total},
    }
    if with_stats:
        # 指标只看非 admin_preview 轮次；列表本身可以出现自测记录（带 channel）。
        counted = [row for row in rows if row["channel"] != "admin_preview"]
        body["stats"] = {
            "rounds": len(counted),
            "identifiableRounds": sum(1 for row in counted if row["engineer_id"] is not None),
        }
    return body


@router.get("/messages/{message_id}/chain")
def get_message_chain(
    message_id: str,
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """单轮问答详情：message（结构化/命中/引用/剥离）+ 同会话多轮（升序）。"""
    numeric = _parse_id(message_id, ("msg-", "qa-"))
    if numeric is None:
        raise _not_found("问答记录不存在")

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("select id, session_id, role from messages where id = %s", (numeric,))
        target = cur.fetchone()
        if target is None:
            raise _not_found("问答记录不存在")
        question_id = int(target["id"])
        if target["role"] == "assistant":
            # 允许用回答 id 打开链路：回退到它前面最近的一条提问
            cur.execute(
                "select id from messages where session_id = %s and role = 'user' "
                "and id < %s order by id desc limit 1",
                (target["session_id"], question_id),
            )
            previous = cur.fetchone()
            if previous is None:
                raise _not_found("问答记录不存在")
            question_id = int(previous["id"])
        session_pk = int(target["session_id"])

        cur.execute(
            _ROUNDS_CTE
            + " select * from rounds where session_id = %s order by question_id asc",
            (session_pk,),
        )
        session_rounds = cur.fetchall()

    selected = next(
        (row for row in session_rounds if int(row["question_id"]) == question_id), None
    )
    if selected is None:
        raise _not_found("问答记录不存在")

    hits = _hits_by_round(conn, [selected])
    return {
        "message": _round_to_contract(selected, hits.get(question_id, [])),
        "rounds": [_round_summary(row) for row in session_rounds],
    }


# ---------------------------------------------------------------- 知识缺口


@router.get("/gaps")
def list_gaps(
    status_filter: GapStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    with_stats: bool = Query(default=False, alias="withStats"),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """缺口清单；先幂等回填/校准聚合快照，再按状态分页。"""
    _sync_gaps(conn)

    where = ["1 = 1"]
    params: list[Any] = []
    if status_filter:
        where.append("status = %s")
        params.append(status_filter)
    clause = " and ".join(where)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"select count(*) as total from gaps where {clause}", params)
        total = int(cur.fetchone()["total"])
        cur.execute(
            f"select * from gaps where {clause} "
            "order by last_at desc, id desc limit %s offset %s",
            [*params, page_size, (page - 1) * page_size],
        )
        rows = cur.fetchall()
        stats = None
        if with_stats:
            cur.execute(
                "select count(*) filter (where status = 'open') as uncovered, "
                "count(*) filter (where status = 'covered') as covered from gaps"
            )
            counts = cur.fetchone()
            stats = {
                "uncovered": int(counts["uncovered"]),
                "covered": int(counts["covered"]),
            }

    return {
        "items": [_gap_to_contract(row) for row in rows],
        "pagination": {"page": page, "pageSize": page_size, "total": total},
        **({"stats": stats} if stats is not None else {}),
    }


@router.patch("/gaps/{gap_id}")
def patch_gap(
    gap_id: str,
    body: GapPatchRequest,
    admin: dict[str, Any] = Depends(require_governance_admin),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """缺口状态流转（认领/关闭/忽略）；「撤销覆盖」把 status 改回 open 并清空覆盖来源。"""
    numeric = _parse_id(gap_id, ("gap-",))
    if numeric is None:
        raise _not_found("知识缺口不存在")

    updates = body.model_dump(exclude_unset=True)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("select * from gaps where id = %s", (numeric,))
        current = cur.fetchone()
        if current is None:
            raise _not_found("知识缺口不存在")

        if "covered_by" in updates:
            covered_by = updates["covered_by"]
        elif body.status == "open":
            covered_by = None
        else:
            covered_by = current["covered_by"]
        note = updates.get("note", current["note"])

        cur.execute(
            "update gaps set status = %s, covered_by = %s, note = %s, updated_at = now() "
            "where id = %s returning *",
            (body.status, covered_by, note, numeric),
        )
        updated = cur.fetchone()
        cur.execute(
            "insert into admin_audit_log "
            "(actor_account, action, target_type, target_id, detail) "
            "values (%s, %s, %s, %s, %s)",
            (
                str(admin.get("account") or ""),
                "gap.patch",
                "gap",
                f"gap-{numeric}",
                Jsonb(
                    {
                        "status": body.status,
                        "coveredBy": covered_by,
                        "previousStatus": current["status"],
                        "note": note,
                    }
                ),
            ),
        )
    conn.commit()
    return _gap_to_contract(updated)


# ---------------------------------------------------------------- 序列化


def _round_to_contract(row: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """一轮问答 → 契约 QaMessage（camelCase）。"""
    structured = row.get("structured_query") or {}
    citations = row.get("citations") or []
    stripped = _STRIPPED if row.get("gate_reason") == "citation_stripped" else []
    asked_at = row.get("asked_at")
    question_at = row.get("question_at") or asked_at
    resolved_in_sec = None
    if row.get("resolved_state") == "resolved" and row.get("resolved_at") and question_at:
        resolved_in_sec = max(0, int((row["resolved_at"] - question_at).total_seconds()))

    return {
        "id": f"msg-{row['question_id']}",
        "time": asked_at.isoformat() if asked_at else None,
        "sessionId": f"ses-{row['session_id']}",
        "user": row.get("engineer_name") or "未识别",
        "ticket": (row.get("host_context") or {}).get("ticketNo"),
        "identity": {
            "source": row["auth_source"],
            "claimed": row.get("claimed_engineer_id"),
            "engineer": row.get("engineer_account"),
        },
        "question": row.get("question") or "",
        "structured": {
            "symptom": structured.get("symptom"),
            "model": structured.get("model"),
            "batch": structured.get("batch"),
            "code": structured.get("code"),
            "missing": list(structured.get("missing") or []),
            "batchOrigin": structured.get("batchOrigin"),
        },
        "hits": hits,
        "citations": len(citations),
        "stripped": stripped,
        "outcome": row["outcome"],
        "resolved": row.get("resolved_state"),
        "resolvedInSec": resolved_in_sec,
        "handoff": bool(row.get("handoff")),
        "channel": row["channel"],
    }


def _round_summary(row: dict[str, Any]) -> dict[str, Any]:
    """一轮问答 → 契约 QaChain.rounds 项（同会话多轮的时间线）。"""
    asked_at = row.get("asked_at")
    answered_at = row.get("answered_at")
    elapsed_ms = 0
    if asked_at and answered_at:
        elapsed_ms = max(0, int((answered_at - asked_at).total_seconds() * 1000))
    gate_reason = row.get("gate_reason")
    structured = row.get("structured_query") or {}
    return {
        "question": row.get("question") or "",
        "answerSummary": _summary(row.get("answer") or ""),
        "outcome": row["outcome"],
        "gateReason": gate_reason if gate_reason in ("gap", "safe", "citation_stripped") else None,
        "citations": list(row.get("citations") or []),
        "stripped": _STRIPPED_ROUND if gate_reason == "citation_stripped" else [],
        "askFields": list(structured.get("missing") or []),
        "elapsedMs": elapsed_ms,
    }


def _summary(text: str, limit: int = 160) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _gap_to_contract(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"gap-{row['id']}",
        "model": row.get("model"),
        "symptom": row.get("symptom") or "",
        "count": int(row.get("count") or 0),
        "firstAt": row["first_at"].isoformat() if row.get("first_at") else None,
        "lastAt": row["last_at"].isoformat() if row.get("last_at") else None,
        "status": row.get("status") or "open",
        "coveredBy": row.get("covered_by"),
        "relatedMessages": list(row.get("related_messages") or []),
    }


# ---------------------------------------------------------------- 命中证据


def _hits_by_round(
    conn: psycopg.Connection, rows: list[dict[str, Any]]
) -> dict[int, list[dict[str, Any]]]:
    """批量组装 hits：优先用落库的最终引用（带 loc/kind/score），
    缺口轮次用 evidence_ids 回查切片的出处（分数未落库，保持 null）。"""
    hits_by_id: dict[int, list[dict[str, Any]]] = {}
    wanted: list[str] = []
    for row in rows:
        hits: list[dict[str, Any]] = []
        seen: set[str] = set()
        for citation in row.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            evidence_id = citation.get("evidenceId")
            hits.append(
                _hit_item(
                    str(evidence_id) if evidence_id else "",
                    citation.get("loc"),
                    citation.get("kind"),
                    citation.get("score"),
                )
            )
            if evidence_id:
                seen.add(str(evidence_id))
        for evidence_id in row.get("evidence_ids") or []:
            if evidence_id and str(evidence_id) not in seen:
                wanted.append(str(evidence_id))
        hits_by_id[int(row["question_id"])] = hits

    meta = _chunk_hit_meta(conn, wanted) if wanted else {}
    for row in rows:
        hits = hits_by_id[int(row["question_id"])]
        seen = {str(hit.get("evidenceId")) for hit in hits if hit.get("evidenceId")}
        for evidence_id in row.get("evidence_ids") or []:
            if not evidence_id or str(evidence_id) in seen:
                continue
            info = meta.get(str(evidence_id), {})
            hits.append(_hit_item(str(evidence_id), info.get("loc"), info.get("kind"), None))
            seen.add(str(evidence_id))
    return hits_by_id


def _hit_item(
    evidence_id: str, loc: Any, kind: Any, score: Any
) -> dict[str, Any]:
    """契约里 hits 的可选字段不放 null：未知就省略，避免伪造"相关度 0"之类的值。"""
    item: dict[str, Any] = {"evidenceId": evidence_id}
    if loc is not None:
        item["loc"] = loc
    if kind is not None:
        item["kind"] = kind
    if score is not None:
        item["score"] = score
    return item


def _chunk_hit_meta(
    conn: psycopg.Connection, evidence_ids: list[str]
) -> dict[str, dict[str, Any]]:
    unique = list(dict.fromkeys(evidence_ids))
    if not unique:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "select c.evidence_id, c.section_no, c.page_from, c.page_to, "
            "c.meta_json, d.source_type "
            "from chunks c join documents d on d.id = c.document_id "
            "where c.evidence_id = any(%s)",
            (unique,),
        )
        result: dict[str, dict[str, Any]] = {}
        for evidence_id, section, page_from, page_to, meta, source_type in cur.fetchall():
            meta = meta or {}
            loc = (
                meta.get("loc")
                or (f"§{section}" if section else None)
                or page_label(page_from, page_to)
            )
            result[str(evidence_id)] = {
                "loc": loc,
                "kind": _KIND_BY_SOURCE.get(str(source_type), "manual"),
            }
        return result


# ---------------------------------------------------------------- 缺口回填


def _symptom_key(text: str) -> str:
    """症状归一化：全角转半角、转小写、去空白与标点，用于把同义提问聚合到同一缺口。"""
    raw = unicodedata.normalize("NFKC", (text or "")).strip().lower()
    return re.sub(r"[\s!-/:-@\[-`{-~，。、；：？！“”‘’（）《》【】…—·]+", "", raw)[:200]


def _sync_gaps(conn: psycopg.Connection) -> None:
    """从 messages 幂等回填/校准 gaps 聚合快照；不清除人工维护的 status/covered_by/note。"""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_GAP_TURNS_SQL)
        turns = cur.fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for turn in turns:
        question = (turn.get("question") or "").strip()
        key = _symptom_key(question)
        if not key:
            continue
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = {
                "symptom": question,
                "model": turn.get("model"),
                "count": 1,
                "first_at": turn["asked_at"],
                "last_at": turn["asked_at"],
                "related": [f"msg-{turn['question_id']}"],
            }
            continue
        entry["count"] += 1
        entry["last_at"] = turn["asked_at"]
        if turn.get("model"):
            entry["model"] = turn["model"]
        related = f"msg-{turn['question_id']}"
        if related not in entry["related"]:
            entry["related"].append(related)

    if not grouped:
        return
    with conn.cursor() as cur:
        for key, entry in grouped.items():
            cur.execute(
                _GAP_UPSERT,
                (
                    key,
                    entry["symptom"],
                    entry["model"],
                    entry["count"],
                    entry["first_at"],
                    entry["last_at"],
                    entry["related"],
                ),
            )
    conn.commit()


# ---------------------------------------------------------------- 参数与错误


def _round_filters(
    *,
    outcome: str | None,
    resolved: str | None,
    identity: str | None,
    date_from: date | None,
    date_to: date | None,
) -> tuple[str, list[Any]]:
    where = ["1 = 1"]
    params: list[Any] = []
    if outcome:
        where.append("outcome = %s")
        params.append(outcome)
    if resolved == "resolved":
        where.append("resolved_state = 'resolved'")
    elif resolved == "unresolved":
        where.append("resolved_state = 'unresolved'")
    elif resolved == "none":
        where.append("resolved_state is null")
    if identity == "unidentified":
        where.append("engineer_id is null")
    elif identity:
        where.append("auth_source = %s")
        params.append(identity)
    if date_from:
        where.append("asked_at >= %s::date")
        params.append(date_from)
    if date_to:
        where.append("asked_at < (%s::date + 1)")
        params.append(date_to)
    return " and ".join(where), params


def _parse_id(raw: str, prefixes: tuple[str, ...]) -> int | None:
    text = (raw or "").strip()
    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return int(text) if text.isdigit() else None


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "NOT_FOUND", "message": message},
    )
