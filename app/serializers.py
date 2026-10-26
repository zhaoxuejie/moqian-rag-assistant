"""数据库行 → 契约 JSON（camelCase）的转换。

集中一处的好处：字段名只在契约里定义一次，路由与测试都调用这些函数，
不会出现"列表接口叫 deviceModel、详情接口叫 model"这种漂移。
"""

from __future__ import annotations

from typing import Any

_DOC_STATUS_TO_LIFECYCLE = {
    "uploaded": "current",
    "parsing": "current",
    "parsed": "current",
    "failed": "current",
    "disabled": "disabled",
}

_JOB_STAGE = {
    "queued": "排队中",
    "running": "解析中",
    "done": "完成",
    "failed": "失败",
    "cancelled": "已取消",
}


def _human_size(size: int | None) -> str | None:
    """文件体积展示：与原型一致（`18.6 MB` / `0.4 MB` / `812 KB`）。"""
    if size is None or size < 0:
        return None
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        kb = size / 1024
        return f"{kb:.0f} KB" if kb >= 100 else f"{kb:.1f} KB"
    return f"{size} B"


def document_to_contract(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("meta_json") or {}
    source_type = row.get("source_type") or "pdf"
    category = meta.get("category") or ("manual" if source_type == "pdf" else source_type)
    return {
        "id": f"doc-{row['id']}",
        "category": category,
        "format": source_type,
        "title": row.get("title") or "",
        "docCode": row.get("doc_code"),
        "model": row.get("device_model"),
        "batch": row.get("batch"),
        "version": row.get("manual_version"),
        "effective": row.get("effective_date").isoformat()
        if row.get("effective_date")
        else None,
        "status": row.get("status") or "uploaded",
        # lifecycle 是独立语义（current/superseded/disabled）；旧数据没有该列时按 status 兜底
        "lifecycle": row.get("lifecycle")
        or _DOC_STATUS_TO_LIFECYCLE.get(row.get("status") or "", "current"),
        "chunks": int(row.get("chunk_count") or 0),
        "size": _human_size(meta.get("sizeBytes")) or meta.get("sizeLabel"),
        "updated": row.get("updated_at").strftime("%Y-%m-%d %H:%M")
        if row.get("updated_at")
        else None,
        "uploader": row.get("uploader"),
        "fileSha256": row.get("file_sha256"),
    }


def _evidence_kinds(row: dict[str, Any]) -> list[str]:
    """证据单元按格式给出处种类：Excel FAQ 是「行号」，PDF 手册是「页码 + 章节」。"""
    if row.get("source_type") == "excel":
        return ["行号"]
    return ["页码", "章节"] if row.get("status") == "parsed" else []


def document_detail_to_contract(row: dict[str, Any]) -> dict[str, Any]:
    base = document_to_contract(row)
    meta = row.get("meta_json") or {}
    base.update(
        {
            "filePath": row.get("file_path"),
            "hasOriginal": bool(row.get("file_path")),
            "parsedAt": meta.get("parsedAt"),
            "chunkStatusCounts": {
                "active": int(row.get("chunk_count") or 0)
                - int(row.get("chunk_disabled_count") or 0),
                "disabled": int(row.get("chunk_disabled_count") or 0),
            },
            "evidenceKinds": _evidence_kinds(row),
            "supersedes": None,
            "parseMeta": {
                "pages": meta.get("pages"),
                "sections": meta.get("sections"),
                "usedToc": meta.get("usedToc"),
                "embeddingModel": meta.get("embeddingModel"),
                "embeddingDim": meta.get("embeddingDim"),
                "embeddingMs": meta.get("embeddingMs"),
                "error": meta.get("error"),
            },
        }
    )
    return base


def page_label(page_from: int | None, page_to: int | None) -> str | None:
    """出处页码显示：单页 `P128`，跨页 `P128–129`（与原型文案一致）。"""
    if not page_from and not page_to:
        return None
    start = page_from or page_to
    end = page_to or page_from
    if start == end:
        return f"P{start}"
    return f"P{start}–{end}"


def chunk_to_contract(row: dict[str, Any]) -> dict[str, Any]:
    tokens = row.get("bm25_tokens") or ""
    loc = row.get("loc") or (f"§{row['section_no']}" if row.get("section_no") else None)
    page = page_label(row.get("page_from"), row.get("page_to"))
    is_row_loc = not page and bool(loc) and "第" in str(loc) and "行" in str(loc)
    if is_row_loc:
        prov_kind = "工作表行号"
    elif page:
        prov_kind = "所属章节 / 页码"
    else:
        # Word / Markdown / TXT 没有可靠分页，出处只有章节
        prov_kind = "所属章节"
    return {
        "id": f"ch-{row['id']}",
        "evidenceId": row.get("evidence_id") or "",
        "loc": loc,
        "page": page,
        "provKind": prov_kind,
        "text": row.get("content") or "",
        "status": "disabled" if row.get("disabled") else "active",
        "model": row.get("device_model"),
        "batch": row.get("batch"),
        "version": row.get("manual_version"),
        "sectionNo": row.get("section_no"),
        "tokens": len(tokens.split()) if tokens else None,
    }


def job_to_contract(row: dict[str, Any]) -> dict[str, Any]:
    status = row.get("status") or "queued"
    payload = row.get("payload") or {}
    document_id = payload.get("documentId")
    job: dict[str, Any] = {
        "id": f"job-{row['id']}",
        "docId": f"doc-{document_id}" if document_id else None,
        "docTitle": row.get("doc_title") or "",
        "stage": _JOB_STAGE.get(status, status),
        "status": status,
        "progress": 100 if status in ("done", "cancelled") else (0 if status == "queued" else 50),
        "cancelRequested": bool(row.get("cancel_requested")),
        "startedAt": row.get("started_at").strftime("%Y-%m-%d %H:%M")
        if row.get("started_at")
        else None,
        "finishedAt": row.get("finished_at").strftime("%Y-%m-%d %H:%M")
        if row.get("finished_at")
        else None,
        "note": row.get("error"),
        "attempts": int(row.get("attempts") or 0),
    }
    # 非文档任务（ping / llm.smoke / 未注册类型）没有知识源：category 与 format 一律省略。
    # 否则左连接出来的空 source_type 会被兜底成 "pdf"，界面上就出现"某份 PDF 的任务"这种假信息。
    if document_id:
        job["category"] = row.get("category") or "manual"
        job["format"] = row.get("source_type") or "pdf"
    return job
