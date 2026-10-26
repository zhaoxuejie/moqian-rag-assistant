"""存量资料建档：把一批文件登记进 `documents` 并排队解析。

用法：
    python -m app.import_docs                 # 扫 data/original-docs（幂等，按 sha256 去重）
    python -m app.import_docs --root data/inbox   # 扫暂存区（会递归进 manual/faq/ticket）
    python -m app.import_docs --no-enqueue    # 只建档不排队
    python -m app.import_docs --parse-now     # 不走 worker，直接同步解析（调试用）

为什么单独一个脚本而不是自动扫描：自动扫描会在启动时意外把新文件入库，
"入库"是治理动作，应由人触发（与 PRD §5.4"人工确认"同一取向）。

两条硬规则：
1. **原件一律落 `data/original-docs/`**。`file_path` 存的是相对 `data/` 的路径，而
   `/admin/documents/{id}/file` 只允许读原件区；从 `inbox/` 导入时会先把文件**复制**进
   原件区（带时间戳前缀，和上传接口同一套命名），源文件保留不动。`--root` 指到 `data/`
   之外会直接拒绝——那样的路径建不了档。
2. **格式白名单与上传接口共用** `app.ingest.SUFFIX_TO_SOURCE`：PDF / Word(.docx) /
   Markdown(.md) / TXT / Excel(.xlsx) / CSV。不在表里的后缀记进"跳过"并给原因，不静默丢。

Excel 的分类按暂存区目录推断（`inbox/faq` → FAQ 一行一条，`inbox/ticket` → 工单导出），
其余默认按**数据表格**（台账 / 记录表，一行一条）解析；Office 的 `~$` 锁文件与 0 字节
文件直接跳过，不占文档编号。
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from modules.tasks import enqueue

from .config import Settings, get_settings
from .db import connect
from .embedding import get_embedding_client
from .ingest import (
    ORIGINAL_DOCS_DIR,
    SUFFIX_TO_SOURCE,
    IngestError,
    file_sha256,
    infer_metadata,
    original_target_path,
    parse_document,
    upsert_document,
)
from .logging_setup import configure_logging

logger = logging.getLogger("app.import_docs")

# 已知入不了库的文件（扫描件无文字层），显式跳过并在输出里说明，不静默失败。
DEFAULT_SKIP_KEYWORDS: tuple[str, ...] = (
    "电梯使用及管理手册",  # 纯扫描件，需 OCR
    "索引表",  # 字体映射损坏（已删除，保留以防再次出现）
)


class SkipFile(Exception):
    """该文件不参与建档，附带一段能照着处理的原因。"""


@dataclass(frozen=True)
class PlannedImport:
    """一个文件怎么入库：登记的 source_type、原件区落点、是否需要复制。"""

    source: Path
    target: Path
    source_type: str
    needs_copy: bool


def _iter_files(root: Path) -> list[Path]:
    """递归收集待建档文件（`--root data/inbox` 能一次收下 manual/faq/ticket）。"""
    if not root.exists():
        raise SystemExit(f"目录不存在：{root}")
    return sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.as_posix(),
    )


def _ensure_root(root: Path, data_dir: Path) -> Path:
    """`--root` 必须在 `data/` 内，否则 `file_path` 无法相对化、也没法被前端读回。"""
    resolved = root.resolve()
    if data_dir not in resolved.parents and resolved != data_dir:
        raise SystemExit(
            f"--root 必须在 data/ 内（当前 {resolved}）。\n"
            f"原因：documents.file_path 存的是相对 data/ 的路径，data/ 之外的文件建不了档。"
        )
    return resolved


def _skip_reason(path: Path) -> str | None:
    """不该建档的文件：Office 临时锁文件、空文件。返回原因，可建档返回 None。"""
    if path.name.startswith("~$"):
        return "Office 临时锁文件（~$ 前缀）"
    try:
        if path.stat().st_size == 0:
            return "空文件（0 字节）"
    except OSError:
        return "读不到文件大小"
    return None


def _category_hint(path: Path, data_dir: Path) -> str | None:
    """按暂存区目录语义给 Excel 定分类：`inbox/faq` → faq，`inbox/ticket` → ticket。

    其余目录返回 None，交给 `infer_metadata` 的格式默认值（表格类默认 `table`）。
    """
    try:
        parts = path.resolve().relative_to(data_dir.resolve()).parts
    except ValueError:
        return None
    lowered = {part.lower() for part in parts[:-1]}
    if "faq" in lowered:
        return "faq"
    if "ticket" in lowered:
        return "ticket"
    return None


def plan_import(path: Path, data_dir: Path) -> PlannedImport:
    """决定一个文件怎么入库（纯路径判断，不碰数据库、不复制）。

    - 已在 `original-docs/`：原地登记，不动文件；
    - 在 `data/` 其它位置（如 `inbox/manual/`）：复制进原件区再登记，源文件保留；
    - 不支持的格式：抛 `SkipFile`，调用方记进"跳过"，不静默丢弃。
    """
    source_type = SUFFIX_TO_SOURCE.get(path.suffix.lower())
    if source_type is None:
        suffix = path.suffix.lower() or "无扩展名"
        raise SkipFile(f"不支持的格式 {suffix}（支持 PDF / Word(.docx) / Markdown / TXT / Excel）")
    source = path.resolve()
    original_dir = (data_dir / ORIGINAL_DOCS_DIR).resolve()
    if source.parent == original_dir:
        return PlannedImport(
            source=source, target=source, source_type=source_type, needs_copy=False
        )
    return PlannedImport(
        source=source,
        target=original_target_path(data_dir, source.name),
        source_type=source_type,
        needs_copy=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="存量资料建档与排队解析")
    parser.add_argument(
        "--root",
        default=None,
        help=f"待建档目录（默认 DATA_DIR/{ORIGINAL_DOCS_DIR}；可指向 data/inbox 等暂存区）",
    )
    parser.add_argument("--no-enqueue", action="store_true", help="只建档，不排队解析")
    parser.add_argument("--parse-now", action="store_true", help="同步解析（不依赖 worker）")
    parser.add_argument(
        "--reparse", action="store_true", help="已存在的文档也重新排队解析（改了切片器后用）"
    )
    parser.add_argument("--include-skipped", action="store_true", help="连已知扫描件也建档")
    args = parser.parse_args(argv)

    settings: Settings = get_settings()
    configure_logging(settings.log_level)
    data_dir = Path(settings.data_dir).resolve()
    root = _ensure_root(
        Path(args.root) if args.root else data_dir / ORIGINAL_DOCS_DIR,
        data_dir,
    )

    created: list[tuple[str, int]] = []
    existing: list[tuple[str, int]] = []
    skipped: list[tuple[str, str]] = []
    copied = 0

    started = time.perf_counter()
    with connect() as conn:
        for path in _iter_files(root):
            reason = _skip_reason(path)
            if reason:
                skipped.append((path.name, reason))
                continue
            known_skip = any(word in path.name for word in DEFAULT_SKIP_KEYWORDS)
            if known_skip and not args.include_skipped:
                skipped.append((path.name, "已知无文字层/字体损坏，跳过"))
                continue
            try:
                plan = plan_import(path, data_dir)
            except SkipFile as exc:
                skipped.append((path.name, str(exc)))
                continue

            if plan.needs_copy:
                plan.target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(plan.source, plan.target)
                landed = plan.target
                copied += 1
            else:
                landed = plan.source

            digest = file_sha256(landed)
            meta = infer_metadata(path.name, path_category=_category_hint(path, data_dir))
            document_id, is_new = upsert_document(
                conn,
                title=path.stem,
                file_path=landed.relative_to(data_dir).as_posix(),
                source_type=plan.source_type,
                sha256=digest,
                meta=meta,
                size_bytes=landed.stat().st_size,
            )
            if plan.needs_copy and not is_new:
                # 与上传接口一致：内容重复就不留在原件区，避免堆一堆同样的副本
                landed.unlink(missing_ok=True)
                copied -= 1
            (created if is_new else existing).append((path.name, document_id))
            if (is_new or args.reparse) and not args.no_enqueue:
                with conn.cursor() as cur:
                    cur.execute(
                        "update documents set status = 'uploaded', updated_at = now() "
                        "where id = %s",
                        (document_id,),
                    )
                conn.commit()
                enqueue(conn, "parse", {"documentId": str(document_id)})

        if args.parse_now:
            embedder = get_embedding_client(settings)
            for name, document_id in created:
                try:
                    result = parse_document(
                        conn, document_id=document_id, settings=settings, embedder=embedder
                    )
                    logger.info(
                        "parsed %s → %d 切片 / %d 页 / toc=%s",
                        name,
                        result.chunks,
                        result.pages,
                        result.used_toc,
                    )
                except IngestError as exc:
                    logger.warning("parse failed %s: %s", name, exc)

    elapsed = time.perf_counter() - started
    print(
        f"建档：新建 {len(created)} 份，已存在 {len(existing)} 份，跳过 {len(skipped)} 份"
        f"（复制进原件区 {copied} 份）"
    )
    for name, document_id in created:
        print(f"  + doc-{document_id}  {name}")
    for name, reason in skipped:
        print(f"  ! 跳过 {name}（{reason}）")
    print(f"耗时 {elapsed:.1f}s；扫描目录 {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
