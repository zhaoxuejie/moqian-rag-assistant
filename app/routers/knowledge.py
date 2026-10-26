"""后台知识源接口（S2 子集）：列表 / 详情 / 切片预览 / 任务看板 / 上传。

口径：
- 切片是知识库原子（architecture §5.1），切片预览必须能直接看到**页码出处**；
- 上传只做"建档 + 排队"，解析在 worker 里跑（大文件不阻塞请求）；
- 统计数字与列表同源（`stats` 与 `items` 用同一 where 条件算）。
"""

from __future__ import annotations

import logging
import mimetypes
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from modules.tasks import enqueue

from .. import serializers
from ..config import Settings, get_settings
from ..ingest import (
    ORIGINAL_DOCS_DIR,
    SUFFIX_TO_SOURCE,
    file_sha256,
    infer_metadata,
    original_target_path,
    upsert_document,
)
from ..schemas import ChunkPatch, DocumentEnableRequest, DocumentPatch, JobActionRequest
from .deps import get_conn, require_governance_admin

logger = logging.getLogger("app.knowledge")

# 「工作表名 + 第N行」型出处（FAQ Excel）：这类 loc 不是章节号，不能补「§」
_ROW_LOC_RE = re.compile(r"第\s*\d+\s*行|工作表")
router = APIRouter(
    prefix="/admin",
    tags=["admin-knowledge"],
    dependencies=[Depends(require_governance_admin)],
)

_DOC_SELECT = """
select d.id, d.source_type, d.title, d.doc_code, d.device_model, d.batch, d.manual_version,
       d.effective_date, d.file_path, d.file_sha256, d.status, d.lifecycle, d.meta_json,
       d.created_at, d.updated_at, null::text as uploader,
       coalesce(c.cnt, 0) as chunk_count,
       coalesce(c.disabled_cnt, 0) as chunk_disabled_count
  from documents d
  left join (
        select document_id,
               count(*) as cnt,
               count(*) filter (where disabled) as disabled_cnt
          from chunks group by document_id
       ) c on c.document_id = d.id
"""

_JOB_SELECT = """
select j.id, j.type, j.payload, j.status, j.error, j.attempts, j.cancel_requested,
       j.created_at, j.started_at, j.finished_at, j.worker_id,
       d.title as doc_title, d.source_type,
       coalesce(d.meta_json ->> 'category', 'manual') as category
  from jobs j
  left join documents d on d.id = nullif(j.payload ->> 'documentId', '')::bigint
"""


def _pagination(page: int, page_size: int, total: int) -> dict[str, Any]:
    return {"page": page, "pageSize": page_size, "total": total}


@router.get("/documents")
def list_documents(
    q: str | None = None,
    category: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    lifecycle: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    with_stats: bool = Query(default=False, alias="withStats"),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    where: list[str] = ["1 = 1"]
    params: list[Any] = []
    if q:
        where.append("(d.title ilike %s or coalesce(d.doc_code, '') ilike %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if category:
        where.append("coalesce(d.meta_json ->> 'category', 'manual') = %s")
        params.append(category)
    if status_filter:
        if status_filter == "disabled":  # 兼容旧口径：停用现在由 lifecycle 表达
            where.append("d.lifecycle = 'disabled'")
        else:
            where.append("d.status = %s")
            params.append(status_filter)
    if lifecycle:
        where.append("d.lifecycle = %s")
        params.append(lifecycle)
    else:
        # 归档默认不进列表（治理视图要干净）；要看得显式传 lifecycle=archived（D-032）
        where.append("d.lifecycle <> 'archived'")
    clause = " and ".join(where)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"select count(*) as total from documents d where {clause}", params)
        total = int(cur.fetchone()["total"])
        cur.execute(
            _DOC_SELECT + f" where {clause} order by d.id desc limit %s offset %s",
            [*params, page_size, (page - 1) * page_size],
        )
        rows = cur.fetchall()

        stats = None
        if with_stats:
            cur.execute(
                "select coalesce(sum(c.cnt), 0) as total_chunks from "
                "(select count(*) as cnt from chunks group by document_id) c"
            )
            total_chunks = int(cur.fetchone()["total_chunks"])
            cur.execute(
                "select coalesce(d.meta_json ->> 'category', 'manual') as category, "
                "count(*) as cnt from documents d group by 1"
            )
            by_category = {row["category"]: int(row["cnt"]) for row in cur.fetchall()}
            cur.execute("select count(*) as cnt from documents where lifecycle = 'current'")
            current_count = int(cur.fetchone()["cnt"])
            # 「近 7 天新增」用滚动窗口，避免自然周边界受服务器时区影响（D-018 收尾）。
            cur.execute(
                "select count(*) as cnt from chunks where created_at >= now() - interval '7 days'"
            )
            added_7d = int(cur.fetchone()["cnt"])
            stats = {
                "total": total,
                "byCategory": by_category,
                "totalChunks": total_chunks,
                "currentVersionCount": current_count,
                "chunksAdded7d": added_7d,
            }

    return {
        "items": [serializers.document_to_contract(row) for row in rows],
        "pagination": _pagination(page, page_size, total),
        **({"stats": stats} if stats else {}),
    }


@router.get("/documents/{document_id}")
def get_document(
    document_id: str,
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    numeric = _parse_id(document_id, "doc-")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_DOC_SELECT + " where d.id = %s", (numeric,))
        row = cur.fetchone()
    if row is None:
        raise _not_found("文档不存在")
    return serializers.document_detail_to_contract(row)


_ORIGINAL_DIR = ORIGINAL_DOCS_DIR
_PREVIEW_TEXT_LIMIT = 1024 * 1024
_TEXT_SUFFIXES = {".txt", ".md", ".log", ".csv"}
# 手册与文档放行 .pdf/.docx/.md/.txt；Excel/CSV 走表格类分类（faq / table）。
# 后缀 → source_type 的唯一口径在 `app.ingest.SUFFIX_TO_SOURCE`，这里只做分类过滤。
_MANUAL_SUFFIXES = frozenset(
    suffix
    for suffix, source_type in SUFFIX_TO_SOURCE.items()
    if source_type not in {"excel", "csv"}
)
_TABLE_SUFFIXES = frozenset(
    suffix for suffix, source_type in SUFFIX_TO_SOURCE.items() if source_type in {"excel", "csv"}
)


def _upload_hint(suffix: str, category: str) -> str:
    """上传被拒时给一句能照着做的提示（不写"格式不对"就完事）。"""
    if category == "faq":
        return "FAQ 表格只支持 .xlsx；请另存为 Excel 后上传（CSV 请选「数据表格」）"
    if category == "table":
        return "数据表格支持 .xlsx / .csv；请检查文件后缀"
    if category == "ticket":
        return "工单数据请上传 Excel / CSV 导出（.xlsx / .csv）；工单批量包暂未开放"
    if suffix in {".xlsx", ".csv"}:
        return "Excel / CSV 请把业务大类选为「FAQ 表格」或「数据表格（参数 / 清单）」"
    if suffix == ".doc":
        return "Word 只支持 .docx，请用 Word「另存为」.docx 后上传"
    if suffix == ".xls":
        return "旧版 .xls 请另存为 .xlsx 后上传"
    return (
        "手册与文档支持 PDF / Word(.docx) / Markdown(.md) / TXT(.txt)；"
        "Excel / CSV 请把业务大类选为「FAQ 表格」或「数据表格」"
    )


def _excel_preview_text(path: Path, limit: int) -> tuple[str, bool]:
    """FAQ 表格式原件 → 逐行纯文本（「第N行 分类｜问题｜答案」），用于核对切片出处。

    只读单元格文本，不求值公式、不渲染样式；超过 limit 字符就截断并告知调用方。
    """
    import openpyxl

    workbook = openpyxl.load_workbook(filename=path, read_only=True, data_only=True)
    lines: list[str] = []
    size = 0
    truncated = False
    try:
        name = "FAQ" if "FAQ" in workbook.sheetnames else workbook.sheetnames[0]
        sheet = workbook[name]
        lines.append(f"工作表：{sheet.title}")
        for row_no, values in enumerate(sheet.iter_rows(values_only=True), start=1):
            cells = [
                str(value).strip()
                for value in values
                if value is not None and str(value).strip()
            ]
            if not cells:
                continue
            line = f"第{row_no}行　" + "　｜　".join(cells)
            size += len(line) + 1
            if size > limit:
                truncated = True
                break
            lines.append(line)
    finally:
        workbook.close()
    return "\n".join(lines), truncated


def _docx_preview_text(path: Path, limit: int) -> tuple[str, bool]:
    """Word 原件 → 纯文本（标题加 `##`、表格逐行），用于离线核对。"""
    from modules.doc_ingest.docx import docx_text_lines

    lines: list[str] = []
    size = 0
    truncated = False
    for line in docx_text_lines(path):
        size += len(line) + 1
        if size > limit:
            truncated = True
            break
        lines.append(line)
    return "\n".join(lines), truncated


def _original_row(conn: psycopg.Connection, document_id: str) -> dict[str, Any]:
    numeric = _parse_id(document_id, "doc-")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select id, source_type, file_path from documents where id = %s",
            (numeric,),
        )
        row = cur.fetchone()
    if row is None:
        raise _not_found("文档不存在")
    return row


def _original_file(settings: Settings, file_path: str | None) -> Path:
    """把库里的相对路径解析成原件；只允许 `data/original-docs` 下的真实文件。"""
    if not file_path:
        raise _not_found("该知识源没有原件（可能由工单沉淀生成）")
    root = Path(settings.data_dir).resolve()
    allowed = (root / _ORIGINAL_DIR).resolve()
    target = (root / file_path).resolve()
    try:
        target.relative_to(allowed)
    except ValueError:
        raise _not_found("原件路径不合法") from None
    if not target.is_file():
        raise _not_found("原件不存在或已被移动")
    return target


@router.get("/documents/{document_id}/file")
def get_document_file(
    document_id: str,
    download: bool = Query(default=False),
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """读取原件：默认 inline 供预览，`?download=1` 走附件下载。

    只按文档 id 取路径，前端拿不到也传不进任意文件路径；PDF 用浏览器内置阅读器打开。
    """
    row = _original_row(conn, document_id)
    target = _original_file(settings, row["file_path"])
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(
        target,
        media_type=media_type,
        filename=target.name,
        content_disposition_type="attachment" if download else "inline",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=60",
        },
    )


@router.get("/documents/{document_id}/preview")
def preview_document(
    document_id: str,
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """原文预览的描述：PDF 交给浏览器阅读器，文本直接回内容。

    Word / Excel 的富文本渲染放在下一版；当前明确回 unsupported，不假装能预览。
    """
    row = _original_row(conn, document_id)
    target = _original_file(settings, row["file_path"])
    suffix = target.suffix.lower()
    if suffix == ".pdf" or (not suffix and row["source_type"] == "pdf"):
        page_width: float | None = None
        page_height: float | None = None
        try:
            import pymupdf

            with pymupdf.open(target) as pdf:
                if pdf.page_count:
                    rect = pdf[0].rect
                    page_width = round(float(rect.width), 2)
                    page_height = round(float(rect.height), 2)
        except Exception:  # noqa: BLE001 - 读不出页尺寸不影响预览本身
            page_width = page_height = None
        return {
            "kind": "pdf",
            "format": "pdf",
            "sizeBytes": target.stat().st_size,
            "pageWidth": page_width,
            "pageHeight": page_height,
        }
    if suffix in _TEXT_SUFFIXES:
        with target.open("rb") as handle:
            raw = handle.read(_PREVIEW_TEXT_LIMIT + 1)
        truncated = len(raw) > _PREVIEW_TEXT_LIMIT
        raw = raw[: _PREVIEW_TEXT_LIMIT]
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030", errors="replace")
        return {
            "kind": "text",
            "format": suffix.lstrip(".") or "text",
            "text": text,
            "truncated": truncated,
            "sizeBytes": target.stat().st_size,
        }
    if suffix in {".xlsx", ".xlsm"}:
        try:
            text, truncated = _excel_preview_text(target, _PREVIEW_TEXT_LIMIT)
        except Exception:  # noqa: BLE001 - 读不出表格就退回"不支持预览"
            text, truncated = "", False
        if text:
            return {
                "kind": "text",
                "format": suffix.lstrip("."),
                "text": text,
                "truncated": truncated,
                "sizeBytes": target.stat().st_size,
            }
    if suffix == ".docx":
        try:
            text, truncated = _docx_preview_text(target, _PREVIEW_TEXT_LIMIT)
        except Exception:  # noqa: BLE001 - 读不出文档就退回"不支持预览"
            text, truncated = "", False
        if not text:
            text = "（该 Word 文档没有可提取的文字，可能是扫描图片；请下载原件查看）"
        return {
            "kind": "text",
            "format": "docx",
            "text": text,
            "truncated": truncated,
            "sizeBytes": target.stat().st_size,
        }
    return {
        "kind": "unsupported",
        "format": suffix.lstrip(".") or row["source_type"],
        "message": "该格式暂不支持在线预览，可下载原件查看。",
    }


@router.get("/documents/{document_id}/chunks")
def list_document_chunks(
    document_id: str,
    q: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    numeric = _parse_id(document_id, "doc-")
    where = ["c.document_id = %s"]
    params: list[Any] = [numeric]
    if q:
        where.append("c.content ilike %s")
        params.append(f"%{q}%")
    if status_filter == "active":
        where.append("c.disabled = false")
    elif status_filter == "disabled":
        where.append("c.disabled = true")
    clause = " and ".join(where)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"select count(*) as total from chunks c where {clause}", params)
        total = int(cur.fetchone()["total"])
        cur.execute(
            """
            select c.id, c.evidence_id, c.content, c.section_no, c.page_from, c.page_to,
                   c.batch, c.manual_version, c.disabled, c.bm25_tokens,
                   d.device_model,
                   coalesce(c.meta_json ->> 'loc', null) as loc
              from chunks c join documents d on d.id = c.document_id
             where """
            + clause
            + " order by c.id limit %s offset %s",
            [*params, page_size, (page - 1) * page_size],
        )
        rows = cur.fetchall()
    return {
        "items": [serializers.chunk_to_contract(row) for row in rows],
        "pagination": _pagination(page, page_size, total),
    }


@router.get("/jobs")
def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    with_stats: bool = Query(default=False, alias="withStats"),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    where = ["1 = 1"]
    params: list[Any] = []
    if status_filter:
        where.append("j.status = %s")
        params.append(status_filter)
    clause = " and ".join(where)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"select count(*) as total from jobs j where {clause}", params)
        total = int(cur.fetchone()["total"])
        cur.execute(
            _JOB_SELECT + f" where {clause} order by j.id desc limit %s offset %s",
            [*params, page_size, (page - 1) * page_size],
        )
        rows = cur.fetchall()
        stats = None
        if with_stats:
            cur.execute("select status, count(*) as cnt from jobs group by status")
            counts = {"queued": 0, "running": 0, "done": 0, "failed": 0, "cancelled": 0}
            for row in cur.fetchall():
                counts[str(row["status"])] = int(row["cnt"])
            stats = {"counts": counts}
    return {
        "items": [serializers.job_to_contract(row) for row in rows],
        "pagination": _pagination(page, page_size, total),
        **({"stats": stats} if stats else {}),
    }


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: str, conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    numeric = _parse_id(job_id, "job-")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "update jobs set status = 'queued', error = null, finished_at = null, worker_id = null "
            "where id = %s and status = 'failed' returning id",
            (numeric,),
        )
        if cur.fetchone() is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "CONFLICT", "message": "只有失败的任务可以重试"},
            )
        cur.execute(_JOB_SELECT + " where j.id = %s", (numeric,))
        row = cur.fetchone()
    conn.commit()
    return serializers.job_to_contract(row)


def _set_document_lifecycle(
    conn: psycopg.Connection,
    numeric: int,
    admin: dict[str, Any],
    *,
    lifecycle: str,
    action: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    """改 lifecycle 并留痕（启停用 / 归档 / 恢复共用，避免三处各写一遍 update）。"""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "update documents set lifecycle = %s, updated_at = now() "
            "where id = %s returning id",
            (lifecycle, numeric),
        )
        if cur.fetchone() is None:
            raise _not_found("文档不存在")
        row = _fetch_document_row(cur, numeric)
        _audit(cur, admin, action, "document", f"doc-{numeric}", detail)
    conn.commit()
    return serializers.document_to_contract(row)


@router.post("/documents/{document_id}/enable")
def enable_document(
    document_id: str,
    body: DocumentEnableRequest,
    admin: dict[str, Any] = Depends(require_governance_admin),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """启停用：切片保留、检索剔除、仍在列表里，可随时恢复。

    与「归档」的区别只在列表可见性（归档默认不出现），语义见 D-032。
    """
    return _set_document_lifecycle(
        conn,
        _parse_id(document_id, "doc-"),
        admin,
        lifecycle="current" if body.enabled else "disabled",
        action="document.enable" if body.enabled else "document.disable",
        detail={"enabled": body.enabled},
    )


@router.post("/documents/{document_id}/archive")
def archive_document(
    document_id: str,
    admin: dict[str, Any] = Depends(require_governance_admin),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """归档：从默认列表移除、退出检索池；切片与原件保留，可恢复（不是物理删除）。"""
    return _set_document_lifecycle(
        conn,
        _parse_id(document_id, "doc-"),
        admin,
        lifecycle="archived",
        action="document.archive",
        detail={"archived": True},
    )


@router.post("/documents/{document_id}/restore")
def restore_document(
    document_id: str,
    admin: dict[str, Any] = Depends(require_governance_admin),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """恢复：归档/停用的知识源回到现行版（重新进列表与检索池）。"""
    return _set_document_lifecycle(
        conn,
        _parse_id(document_id, "doc-"),
        admin,
        lifecycle="current",
        action="document.restore",
        detail={"archived": False},
    )


@router.patch("/documents/{document_id}")
def patch_document(
    document_id: str,
    body: DocumentPatch,
    admin: dict[str, Any] = Depends(require_governance_admin),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """改元数据 / 生命周期。只改显式传入的字段，未传字段保持原值。"""
    numeric = _parse_id(document_id, "doc-")
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise _validation_error("没有需要修改的字段")
    for field in ("doc_code", "model", "batch", "version"):
        if field in updates and updates[field] is not None:
            text = str(updates[field]).strip()
            if text in ("", "—"):
                updates[field] = None

    sets: list[str] = []
    params: list[Any] = []
    for field, column in _DOC_PATCH_COLUMNS.items():
        if field in updates:
            sets.append(f"{column} = %s")
            params.append(updates[field])
    sets.append("updated_at = now()")

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"update documents set {', '.join(sets)} where id = %s returning id",
            [*params, numeric],
        )
        if cur.fetchone() is None:
            raise _not_found("文档不存在")
        # 批次/版本是切片级展示字段，文档改了要同步到切片（多版本冲突展示依赖它）。
        # 但 FAQ 行级批次（meta_json.batchFromRow）优先：文档级批次只填「行内没标过」的
        # 切片，绝不覆盖行内自带的值；清空文档批次同理，行级批次保持不动。
        if "batch" in updates:
            cur.execute(
                "update chunks set batch = %s where document_id = %s "
                "and coalesce(meta_json ->> 'batchFromRow', 'false') <> 'true'",
                (updates["batch"], numeric),
            )
        if "version" in updates:
            cur.execute(
                "update chunks set manual_version = coalesce(%s, manual_version) "
                "where document_id = %s",
                (updates["version"], numeric),
            )
        row = _fetch_document_row(cur, numeric)
        _audit(
            cur,
            admin,
            "document.patch",
            "document",
            f"doc-{numeric}",
            body.model_dump(mode="json", exclude_unset=True),
        )
    conn.commit()
    return serializers.document_to_contract(row)


@router.get("/documents/{document_id}/versions")
def list_document_versions(
    document_id: str,
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """同一文件编号（其次同型号）的版本谱系，按生效日期倒序。"""
    numeric = _parse_id(document_id, "doc-")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select id, doc_code, device_model from documents where id = %s",
            (numeric,),
        )
        target = cur.fetchone()
        if target is None:
            raise _not_found("文档不存在")
        if target["doc_code"]:
            cur.execute(
                _DOC_SELECT
                + " where d.id = %s or d.doc_code = %s "
                "order by d.effective_date desc nulls last, d.id desc",
                (numeric, target["doc_code"]),
            )
        elif target["device_model"]:
            cur.execute(
                _DOC_SELECT
                + " where d.id = %s or d.device_model = %s "
                "order by d.effective_date desc nulls last, d.id desc",
                (numeric, target["device_model"]),
            )
        else:
            cur.execute(_DOC_SELECT + " where d.id = %s", (numeric,))
        rows = cur.fetchall()
    return {"items": [serializers.document_to_contract(row) for row in rows]}


@router.post("/documents/{document_id}/reparse", status_code=status.HTTP_202_ACCEPTED)
def reparse_document(
    document_id: str,
    admin: dict[str, Any] = Depends(require_governance_admin),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """重新解析：排队 parse 任务；同文档已有排队/运行任务时拒绝，避免重复解析。"""
    numeric = _parse_id(document_id, "doc-")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("select id from documents where id = %s", (numeric,))
        if cur.fetchone() is None:
            raise _not_found("文档不存在")
        cur.execute(
            "select id from jobs where type = 'parse' and status in ('queued', 'running') "
            "and payload ->> 'documentId' = %s limit 1",
            (str(numeric),),
        )
        if cur.fetchone() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "CONFLICT",
                    "message": "该文档已有排队中或运行中的解析任务",
                },
            )

    job_id = enqueue(conn, "parse", {"documentId": str(numeric)})
    with conn.cursor(row_factory=dict_row) as cur:
        row = _fetch_job_row(cur, job_id)
        _audit(
            cur,
            admin,
            "document.reparse",
            "document",
            f"doc-{numeric}",
            {"jobId": f"job-{job_id}"},
        )
    conn.commit()
    return serializers.job_to_contract(row)


@router.patch("/chunks/{chunk_id}")
def patch_chunk(
    chunk_id: str,
    body: ChunkPatch,
    admin: dict[str, Any] = Depends(require_governance_admin),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """编辑出处元数据 / 启停用切片。

    M0 不开放正文编辑：改正文必须重算分词与向量（异步任务），当前没有 reindex handler，
    明确返回 422，避免"改了正文但检索/引用仍用旧向量"的假成功。
    """
    numeric = _parse_id(chunk_id, "ch-")
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise _validation_error("没有需要修改的字段")
    if "text" in updates:
        raise _validation_error("切片正文编辑需重算向量，本批次未开放")
    if "model" in updates:
        raise _validation_error("设备型号归属知识源，不支持切片级修改")

    # 前端表格里的占位符（— / 未标注章节）不是真实值，统一归一为 null
    if "section_no" in updates:
        raw = updates["section_no"]
        text = str(raw).strip() if raw is not None else ""
        updates["section_no"] = None if text in ("", "—", "未标注章节") else text
    for field in ("batch", "version"):
        if field in updates and updates[field] is not None:
            text = str(updates[field]).strip()
            updates[field] = None if text in ("", "—") else text

    sets: list[str] = []
    params: list[Any] = []
    if "status" in updates:
        sets.append("disabled = %s")
        params.append(updates["status"] == "disabled")
    if "section_no" in updates:
        sets.append("section_no = %s")
        params.append(updates["section_no"])
        if updates["section_no"] is None:
            sets.append("meta_json = jsonb_set(meta_json, '{loc}', to_jsonb('未标注章节'::text))")
        else:
            section = str(updates["section_no"]).strip()
            # 行号出处（FAQ 工作表：`FAQ 第2行`）不是章节号，加「§」会变成「§FAQ 第2行」，
            # 前端「工作表行号」就显示错了；只有章节号型才补「§」。
            loc = section if section.startswith("§") or _ROW_LOC_RE.search(section) else (
                f"§{section}"
            )
            sets.append("meta_json = jsonb_set(meta_json, '{loc}', to_jsonb(%s::text))")
            params.append(loc)
    if "batch" in updates:
        sets.append("batch = %s")
        params.append(updates["batch"])
    if "version" in updates:
        sets.append("manual_version = %s")
        params.append(updates["version"])

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"update chunks set {', '.join(sets)} where id = %s returning id",
            [*params, numeric],
        )
        if cur.fetchone() is None:
            raise _not_found("切片不存在")
        cur.execute(_CHUNK_SELECT + " where c.id = %s", (numeric,))
        row = cur.fetchone()
        _audit(
            cur,
            admin,
            "chunk.patch",
            "chunk",
            f"ch-{numeric}",
            body.model_dump(mode="json", exclude_unset=True),
        )
    conn.commit()
    return serializers.chunk_to_contract(row)


@router.patch("/jobs/{job_id}")
def patch_job(
    job_id: str,
    body: JobActionRequest,
    admin: dict[str, Any] = Depends(require_governance_admin),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """取消任务（暂停/继续本批次未开放）。"""
    if body.action != "cancel":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": "任务暂停/继续本批次未开放"},
        )
    numeric = _parse_id(job_id, "job-")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "update jobs set status = 'cancelled', finished_at = now(), error = null, "
            "cancel_requested = false where id = %s and status = 'queued' returning id",
            (numeric,),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "update jobs set cancel_requested = true "
                "where id = %s and status = 'running' returning id",
                (numeric,),
            )
            row = cur.fetchone()
        if row is None:
            cur.execute("select status from jobs where id = %s", (numeric,))
            existing = cur.fetchone()
            if existing is None:
                raise _not_found("任务不存在")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "CONFLICT",
                    "message": "只有排队中或运行中的任务可以取消",
                },
            )
        job_row = _fetch_job_row(cur, numeric)
        _audit(
            cur,
            admin,
            "job.cancel",
            "job",
            f"job-{numeric}",
            {"requestedStatus": job_row["status"]},
        )
    conn.commit()
    return serializers.job_to_contract(job_row)


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
def upload_document(
    file: UploadFile = File(...),
    category: str = Form(default="manual"),
    title: str | None = Form(default=None),
    device_model: str | None = Form(default=None, alias="deviceModel"),
    batch: str | None = Form(default=None),
    manual_version: str | None = Form(default=None, alias="manualVersion"),
    effective_date: str | None = Form(default=None, alias="effectiveDate"),
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """上传原件 → 建档 → 排队解析。

    返回 `{documentId, status, jobId}`；解析进度看 `/admin/jobs`。
    原件只落盘不读回，`file_sha256` 用于去重（同一文件重复上传不会产生第二份文档）。
    """
    filename = Path(file.filename or "upload.pdf").name
    suffix = Path(filename).suffix.lower()
    category = (category or "manual").strip().lower()
    if category == "faq":
        allowed = {".xlsx"}
    elif category in {"table", "ticket"}:
        allowed = _TABLE_SUFFIXES
    else:
        allowed = _MANUAL_SUFFIXES
    if suffix not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "VALIDATION_ERROR", "message": _upload_hint(suffix, category)},
        )
    source_type = SUFFIX_TO_SOURCE.get(suffix, "pdf")

    # 生效日期独立成字段（契约 effectiveDate）：以前的表单把「版本 / 日期」塞进一个输入框，
    # 结果整串落到 manual_version、effective_date 永远为空；这里按 ISO 日期严格校验。
    parsed_effective: date | None = None
    if effective_date:
        try:
            parsed_effective = date.fromisoformat(effective_date.strip())
        except ValueError:
            raise _validation_error("生效日期需为 YYYY-MM-DD（如 2025-06-01）") from None

    target = original_target_path(settings.data_dir, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    digest = file_sha256(target)
    meta = infer_metadata(filename)
    meta["category"] = category
    document_id, is_new = upsert_document(
        conn,
        title=title or Path(filename).stem,
        file_path=target.relative_to(Path(settings.data_dir).resolve()).as_posix(),
        source_type=source_type,
        sha256=digest,
        meta=meta,
        size_bytes=target.stat().st_size,
    )
    if not is_new:
        target.unlink(missing_ok=True)
    if device_model or batch or manual_version or parsed_effective:
        with conn.cursor() as cur:
            cur.execute(
                "update documents set device_model = coalesce(%s, device_model), "
                "batch = coalesce(%s, batch), manual_version = coalesce(%s, manual_version), "
                "effective_date = coalesce(%s::date, effective_date), "
                "updated_at = now() where id = %s",
                (device_model, batch, manual_version, parsed_effective, document_id),
            )
        conn.commit()

    job_id = enqueue(conn, "parse", {"documentId": str(document_id)}) if is_new else None
    logger.info("upload doc %s new=%s job=%s", document_id, is_new, job_id)
    return {
        "documentId": f"doc-{document_id}",
        "status": "uploaded" if is_new else "already_exists",
        "jobId": f"job-{job_id}" if job_id else None,
    }


_DOC_PATCH_COLUMNS = {
    "title": "title",
    "doc_code": "doc_code",
    "model": "device_model",
    "batch": "batch",
    "version": "manual_version",
    "effective": "effective_date",
    "lifecycle": "lifecycle",
}

_CHUNK_SELECT = """
select c.id, c.evidence_id, c.content, c.section_no, c.page_from, c.page_to,
       c.batch, c.manual_version, c.disabled, c.bm25_tokens,
       d.device_model,
       coalesce(c.meta_json ->> 'loc', null) as loc
  from chunks c join documents d on d.id = c.document_id
"""


def _fetch_document_row(cur: psycopg.Cursor, numeric: int) -> dict[str, Any]:
    cur.execute(_DOC_SELECT + " where d.id = %s", (numeric,))
    row = cur.fetchone()
    if row is None:
        raise _not_found("文档不存在")
    return row


def _fetch_job_row(cur: psycopg.Cursor, numeric: int) -> dict[str, Any]:
    cur.execute(_JOB_SELECT + " where j.id = %s", (numeric,))
    row = cur.fetchone()
    if row is None:
        raise _not_found("任务不存在")
    return row


def _audit(
    cur: psycopg.Cursor,
    admin: dict[str, Any],
    action: str,
    target_type: str,
    target_id: str,
    detail: dict[str, Any],
) -> None:
    """治理写操作留痕：谁、何时、对哪个对象、改了什么。"""
    cur.execute(
        "insert into admin_audit_log (actor_account, action, target_type, target_id, detail) "
        "values (%s, %s, %s, %s, %s)",
        (str(admin.get("account") or ""), action, target_type, target_id, Jsonb(detail)),
    )


def _validation_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "VALIDATION_ERROR", "message": message},
    )


def _parse_id(raw: str, prefix: str) -> int:
    text = raw.strip()
    if text.startswith(prefix):
        text = text[len(prefix) :]
    if not text.isdigit():
        raise _not_found("资源不存在")
    return int(text)


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail={"code": "NOT_FOUND", "message": message}
    )
