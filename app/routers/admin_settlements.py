"""后台「知识沉淀」接口（PRD FR-K5 / 纪律 8）。

口径（与 `contracts/openapi.yaml` 的 Settlement / SettlementDecision / SettlementPage 一一对应）：

- 沉淀条目是**候选知识**，不是知识；只有管理员显式确认才入库（纪律 8，禁止自动发布）；
- 确认 = 生成一份 `source_type='ticket'` 的知识源 + 一条证据单元（切片），
  正文与出处定位允许在确认时修正，审核留痕写进 `settlements.review_log`；
- 驳回 = 只改状态与留痕，不入库；
- 已入库的条目此后与普通证据单元同等管理（在「知识源管理 / 切片预览」里继续治理）。

已知限制（M0 范围）：
- 确认时会尝试生成查询向量：成功则 Dense + BM25 都可命中；向量服务失败时保留 NULL，
  仍可用 BM25，并把失败状态随确认响应返回，不阻断入库。
- 证据 ID 复用 `app.ingest.evidence_id_for("ticket", document_id, seq)`（`T{文档}{序号}`），
  与手册切片同一套规则；插入前再做一次唯一性探测，避免与历史数据撞号。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import Field

from modules.llm import EmbeddingClient

from ..config import Settings, get_settings
from ..ingest import evidence_id_for
from ..schemas import CamelModel
from ..settlements import SettlementError, create_from_ticket
from .deps import get_conn, get_embedder, require_governance_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin-governance"],
    dependencies=[Depends(require_governance_admin)],
)

_SETTLEMENT_STATUS = ("pending", "confirmed", "rejected")
_SETTLEMENT_CHANNELS = ("ticket", "favorite", "manual")

_SELECT = """
select id, channel, status, title, source, model, batch, version, submitted_by,
       submitted_at, loc, body, context, review_log,
       confirmed_document_id, confirmed_chunk_id
  from settlements
"""


class SettlementDecision(CamelModel):
    """`POST /admin/settlements/{id}/confirm|reject` 的请求体。

    `note` 是审核理由（留痕）；`text` / `loc` 只在确认时允许覆盖，
    对应契约里"确认前可修正正文与出处定位"。
    """

    note: str | None = None
    text: str | None = None
    loc: str | None = None


class SettlementFromTicketRequest(CamelModel):
    """`POST /admin/settlements/from-ticket`：工单完成的「一键沉淀」触发体。

    `sessionId` 只提供工单锚点（`sessions.host_context.ticketNo`）；
    聚合范围按**工单号**跨会话收集已解决轮次，不局限这一个会话。
    """

    session_id: str = Field(alias="sessionId", min_length=1)
    note: str | None = Field(default=None, max_length=2000)


@router.get("/settlements")
def list_settlements(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    status_filter: Literal["pending", "confirmed", "rejected"] | None = Query(
        default=None, alias="status"
    ),
    channel: Literal["ticket", "favorite", "manual"] | None = None,
    with_stats: bool = Query(default=False, alias="withStats"),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """沉淀待确认清单：分页 + 状态/通道过滤 + 可选的全局状态计数。"""
    where: list[str] = ["1 = 1"]
    params: list[Any] = []
    if status_filter:
        where.append("status = %s")
        params.append(status_filter)
    if channel:
        where.append("channel = %s")
        params.append(channel)
    clause = " and ".join(where)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"select count(*) as total from settlements where {clause}", params)
        total = int(cur.fetchone()["total"])
        cur.execute(
            _SELECT + f" where {clause} order by submitted_at desc, id desc limit %s offset %s",
            [*params, page_size, (page - 1) * page_size],
        )
        rows = cur.fetchall()
        stats = None
        if with_stats:
            # 统计与筛选条件无关：三张卡片表达"全库待确认 / 已入库 / 已驳回"，
            # 与列表页脚"共 N 条"一样，数字只有一个来源（数据库计数）。
            cur.execute("select status, count(*) as cnt from settlements group by status")
            counts = {key: 0 for key in _SETTLEMENT_STATUS}
            for row in cur.fetchall():
                counts[str(row["status"])] = int(row["cnt"])
            # 第四张卡「两条通道」需要按来源拆开（工单 / 收藏 / 手工），与原型一致
            cur.execute("select channel, count(*) as cnt from settlements group by channel")
            channels = {key: 0 for key in _SETTLEMENT_CHANNELS}
            for row in cur.fetchall():
                channels[str(row["channel"])] = int(row["cnt"])
            stats = {**counts, "channels": channels}

    return {
        "items": [_to_contract(row) for row in rows],
        "pagination": {"page": page, "pageSize": page_size, "total": total},
        **({"stats": stats} if stats is not None else {}),
    }


@router.post("/settlements/from-ticket")
def create_settlement_from_ticket(
    body: SettlementFromTicketRequest,
    admin: dict[str, Any] = Depends(require_governance_admin),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """工单完成 → 待确认候选（channel=ticket，FR-K5 第二条进水管）。

    当前产品没有独立工单表，`sessions.host_context.ticketNo` 是唯一锚点；
    本接口表达"工单已完成"的人工触发（P0 不写回宿主工单系统）。按工单号聚合
    已解决轮次，产出「故障现象 + 处理方式」候选；是否入库仍由审核页逐条确认。
    幂等：同一工单已有待确认/已入库候选时返回既有条目，`created=false`。
    """
    try:
        settlement_id, created = create_from_ticket(
            conn,
            session_id=body.session_id,
            triggered_by=_operator(admin),
            note=body.note,
        )
    except SettlementError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT + " where id = %s", (settlement_id,))
        row = cur.fetchone()
    if row is None:  # 理论上不可达：刚写入或刚查到
        raise _not_found("沉淀条目不存在")
    return {**_to_contract(row), "created": created}


@router.post("/settlements/{settlement_id}/confirm")
def confirm_settlement(
    settlement_id: str,
    body: SettlementDecision,
    admin: dict[str, Any] = Depends(require_governance_admin),
    conn: psycopg.Connection = Depends(get_conn),
    embedder: EmbeddingClient = Depends(get_embedder),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """确认入库：仅 pending 可确认；生成知识源 + 一条证据单元后状态置 confirmed。

    整个动作在一个事务里完成：文档/切片写入失败时沉淀状态不会变成"已入库"。
    """
    numeric = _parse_id(settlement_id)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT + " where id = %s for update", (numeric,))
        row = cur.fetchone()
        if row is None:
            raise _not_found("沉淀条目不存在")
        if row["status"] != "pending":
            conn.rollback()
            raise _conflict("只有待确认的沉淀条目可以确认入库")

        text = (body.text if body.text is not None else row["body"] or "").strip()
        if not text:
            conn.rollback()
            raise _validation_error("确认入库前正文不能为空")
        raw_loc = body.loc if body.loc is not None else row["loc"]
        loc = _clean_text(raw_loc) or None

        document_id = _insert_ticket_document(cur, row, loc, admin)
        chunk_id, _ = _insert_ticket_chunk(
            cur, document_id=document_id, row=row, text=text, loc=loc
        )
        vectorization = _vectorize_chunk(
            cur, embedder=embedder, chunk_id=chunk_id, text=text, dim=settings.embedding_dim
        )

        entry = _review_entry(
            admin,
            action="confirmed",
            note=_clean_text(body.note) or "确认入库：按确认正文生成证据单元",
        )
        cur.execute(
            """
            update settlements
               set status = 'confirmed',
                   body = %s,
                   loc = %s,
                   review_log = review_log || %s::jsonb,
                   confirmed_document_id = %s,
                   confirmed_chunk_id = %s,
                   updated_at = now()
             where id = %s
            """,
            (text, loc, Jsonb([entry]), document_id, chunk_id, numeric),
        )
        cur.execute(_SELECT + " where id = %s", (numeric,))
        updated = cur.fetchone()
    conn.commit()
    return {**_to_contract(updated), "vectorization": vectorization}


@router.post("/settlements/{settlement_id}/reject")
def reject_settlement(
    settlement_id: str,
    body: SettlementDecision,
    admin: dict[str, Any] = Depends(require_governance_admin),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """驳回：仅 pending 可驳回；理由必填（admin spec §4.5：理由用于复盘与培训）。"""
    numeric = _parse_id(settlement_id)
    note = _clean_text(body.note)
    if not note:
        raise _validation_error("驳回必须填写理由")

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SELECT + " where id = %s for update", (numeric,))
        row = cur.fetchone()
        if row is None:
            raise _not_found("沉淀条目不存在")
        if row["status"] != "pending":
            conn.rollback()
            raise _conflict("只有待确认的沉淀条目可以驳回")

        entry = _review_entry(admin, action="rejected", note=note)
        cur.execute(
            """
            update settlements
               set status = 'rejected',
                   review_log = review_log || %s::jsonb,
                   updated_at = now()
             where id = %s
            """,
            (Jsonb([entry]), numeric),
        )
        cur.execute(_SELECT + " where id = %s", (numeric,))
        updated = cur.fetchone()
    conn.commit()
    return _to_contract(updated)


# ---------- 入库：知识源 + 证据单元 ----------
def _insert_ticket_document(
    cur: psycopg.Cursor,
    row: dict[str, Any],
    loc: str | None,
    admin: dict[str, Any],
) -> int:
    """按沉淀条目生成知识源（source_type=ticket）。

    `source`（工单号/会话/文档）在 documents 没有独立列，随上下文写入 meta_json，
    保证「确认后来源上下文一并保留」（admin spec §4.5）；标题/型号/批次/版本取沉淀字段。
    """
    meta = {
        "category": "ticket",
        "source": row["source"],
        "settlementId": f"st-{int(row['id'])}",
        "loc": loc,
        "context": row["context"] or [],
        "confirmedBy": _operator(admin),
        "confirmedAt": _now_iso(),
    }
    cur.execute(
        """
        insert into documents
            (source_type, title, device_model, batch, manual_version, status, meta_json)
        values ('ticket', %s, %s, %s, %s, 'parsed', %s::jsonb)
        returning id
        """,
        (row["title"], row["model"], row["batch"], row["version"], Jsonb(meta)),
    )
    return int(cur.fetchone()["id"])


def _insert_ticket_chunk(
    cur: psycopg.Cursor,
    *,
    document_id: int,
    row: dict[str, Any],
    text: str,
    loc: str | None,
) -> tuple[int, str]:
    """生成一条证据单元：正文 = 确认正文，loc 写入 meta_json（ticket 没有页码/章节）。

    `bm25_tokens` 落 jieba 预分词，保证关键词召回可用；`embedding` 保持空（见模块文档）。
    """
    evidence_id = _next_evidence_id(cur, document_id)
    meta = {
        "loc": loc,
        "settlementId": f"st-{int(row['id'])}",
        "source": row["source"],
        "title": row["title"],
    }
    cur.execute(
        """
        insert into chunks
            (document_id, evidence_id, content, batch, manual_version, meta_json, bm25_tokens)
        values (%s, %s, %s, %s, %s, %s::jsonb, %s)
        returning id
        """,
        (
            document_id,
            evidence_id,
            text,
            row["batch"],
            row["version"],
            Jsonb(meta),
            _tokenize(text),
        ),
    )
    return int(cur.fetchone()["id"]), evidence_id


def _next_evidence_id(cur: psycopg.Cursor, document_id: int) -> str:
    """按 `evidence_id_for` 生成证据 ID，并探测唯一性（chunks.evidence_id 有唯一索引）。"""
    for seq in range(1, 1000):
        evidence_id = evidence_id_for("ticket", document_id, seq)
        cur.execute("select 1 from chunks where evidence_id = %s", (evidence_id,))
        if cur.fetchone() is None:
            return evidence_id
    raise _conflict("证据 ID 生成失败：请稍后重试或联系管理员")


def _bounded_embedder(embedder: EmbeddingClient) -> EmbeddingClient:
    """确认是同步动作：把向量调用超时压到 10s，避免向量服务挂起时确认按钮长时间等待。"""
    try:
        config = embedder.config
        if float(config.timeout_seconds) > 10:
            return EmbeddingClient(replace(config, timeout_seconds=10.0))
    except Exception:  # noqa: BLE001 - 测试假客户端可能没有 config
        pass
    return embedder


def _vectorize_chunk(
    cur: psycopg.Cursor,
    *,
    embedder: EmbeddingClient | None,
    chunk_id: int,
    text: str,
    dim: int,
) -> dict[str, Any]:
    """为刚入库的 ticket 切片补查询向量；失败不阻断入库，返回可展示状态。"""
    if embedder is None:
        return {"status": "skipped", "message": "未配置向量客户端，切片仅关键词可检索"}
    try:
        vectors = _bounded_embedder(embedder).embed([text])
    except Exception as exc:  # noqa: BLE001 - 外部服务失败必须降级
        logger.warning("settlement vectorize failed (embed): %s", exc)
        return {
            "status": "failed",
            "message": "向量服务不可用：已入库，当前仅关键词可检索，可稍后重试",
        }
    vector = vectors[0] if vectors else []
    if len(vector) != int(dim):
        logger.warning(
            "settlement vectorize dimension mismatch: got=%s want=%s", len(vector), dim
        )
        return {
            "status": "failed",
            "message": f"向量维度 {len(vector)} 与库 {dim} 不一致：已入库，当前仅关键词可检索",
        }
    literal = "[" + ",".join(f"{float(value):.6f}" for value in vector) + "]"
    # 用 savepoint 隔离：即使向量写入失败，确认入库本身仍然可以提交。
    cur.execute("savepoint vectorize_chunk")
    try:
        cur.execute("update chunks set embedding = %s::vector where id = %s", (literal, chunk_id))
        cur.execute("release savepoint vectorize_chunk")
    except Exception as exc:  # noqa: BLE001
        cur.execute("rollback to savepoint vectorize_chunk")
        logger.warning("settlement vectorize failed (write): %s", exc)
        return {
            "status": "failed",
            "message": "向量写入失败：已入库，当前仅关键词可检索，可稍后重试",
        }
    return {"status": "ready", "dim": len(vector)}


# ---------- 契约映射 / 小工具 ----------
def _to_contract(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"st-{int(row['id'])}",
        "channel": row["channel"],
        "status": row["status"],
        "title": row["title"],
        "source": row["source"],
        "model": row["model"],
        "batch": row["batch"],
        "version": row["version"],
        "submittedBy": row["submitted_by"],
        "submittedAt": row["submitted_at"].isoformat() if row["submitted_at"] else None,
        "loc": row["loc"],
        "text": row["body"] or "",
        "context": row["context"] or [],
        "reviewLog": row["review_log"] or [],
    }


def _parse_id(raw: str) -> int:
    text = raw.strip()
    if text.startswith("st-"):
        text = text[len("st-") :]
    if not text.isdigit():
        raise _not_found("沉淀条目不存在")
    return int(text)


def _operator(admin: dict[str, Any]) -> str:
    """审核记录里的操作人：优先姓名，缺省回退工号。"""
    return str(admin.get("name") or admin.get("account") or "管理员")


def _now_iso() -> str:
    """审核留痕时间：ISO 8601（UTC 偏移），前端按浏览器本地时区展示。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _review_entry(admin: dict[str, Any], *, action: str, note: str) -> dict[str, Any]:
    return {"at": _now_iso(), "by": _operator(admin), "action": action, "note": note}


def _clean_text(value: str | None) -> str:
    return (value or "").strip()


def _tokenize(text: str) -> str:
    """与入库流水线一致的 jieba 预分词（BM25 直接消费这份 token）。"""
    import jieba

    return " ".join(token for token in jieba.cut_for_search(text) if token.strip())


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "NOT_FOUND", "message": message},
    )


def _conflict(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "CONFLICT", "message": message},
    )


def _validation_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "VALIDATION_ERROR", "message": message},
    )
