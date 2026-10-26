"""批量建档（`app/import_docs.py`）的纯逻辑测试：不连库、不解析、不下载。

覆盖三件事：
1. 格式白名单与上传接口**共用一张表**（`app.ingest.SUFFIX_TO_SOURCE`），不会各写一份；
2. 原件落点规则：已在原件区就原地登记，在 `inbox/` 则复制进原件区，不支持的格式给原因跳过；
3. `--root` 守在 `data/` 内，避免建出无法相对化的 `file_path`（否则预览必然 404）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.import_docs import (
    SkipFile,
    _category_hint,
    _ensure_root,
    _iter_files,
    _skip_reason,
    plan_import,
)
from app.ingest import ORIGINAL_DOCS_DIR, SUFFIX_TO_SOURCE, infer_metadata, original_target_path


def test_suffix_table_covers_all_supported_formats() -> None:
    assert SUFFIX_TO_SOURCE[".pdf"] == "pdf"
    assert SUFFIX_TO_SOURCE[".docx"] == "docx"
    assert SUFFIX_TO_SOURCE[".md"] == SUFFIX_TO_SOURCE[".markdown"] == "md"
    assert SUFFIX_TO_SOURCE[".txt"] == "txt"
    assert SUFFIX_TO_SOURCE[".xlsx"] == "excel"
    assert SUFFIX_TO_SOURCE[".csv"] == "csv"


def test_upload_whitelist_derived_from_same_table() -> None:
    """上传接口的手册白名单 = 表里非表格类后缀：加格式只改一处，不会两张表打架。"""
    from app.routers.knowledge import _MANUAL_SUFFIXES

    expected = {
        suffix for suffix, source in SUFFIX_TO_SOURCE.items() if source not in {"excel", "csv"}
    }
    assert _MANUAL_SUFFIXES == expected


def test_file_already_in_original_docs_is_registered_in_place(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    original = data_dir / ORIGINAL_DOCS_DIR / "制动器要点.docx"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"docx-bytes")

    plan = plan_import(original, data_dir)

    assert plan.needs_copy is False
    assert plan.target == original.resolve()
    assert plan.source_type == "docx"


def test_inbox_file_is_copied_into_original_docs(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    staged = data_dir / "inbox" / "manual" / "制动器问答.md"
    staged.parent.mkdir(parents=True)
    staged.write_text("# 保养周期\n按型号手册执行。", encoding="utf-8")

    plan = plan_import(staged, data_dir)

    assert plan.needs_copy is True
    assert plan.source_type == "md"
    assert plan.target.parent == (data_dir / ORIGINAL_DOCS_DIR).resolve()
    assert plan.target.name.endswith("-制动器问答.md")
    # 落点必须在 data/ 内，否则 file_path 无法相对化、前端读不回来
    plan.target.relative_to(data_dir.resolve())
    # 计划阶段不落盘，也不动源文件
    assert not plan.target.exists()
    assert staged.exists()


def test_unsupported_suffix_is_skipped_with_reason(tmp_path: Path) -> None:
    path = tmp_path / "旧版手册.doc"
    path.write_bytes(b"old-doc")

    with pytest.raises(SkipFile) as excinfo:
        plan_import(path, tmp_path)

    message = str(excinfo.value)
    assert ".doc" in message
    assert "不支持的格式" in message


def test_root_outside_data_is_rejected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = tmp_path / "下载"
    outside.mkdir()

    with pytest.raises(SystemExit) as excinfo:
        _ensure_root(outside, data_dir)

    assert "必须在 data/ 内" in str(excinfo.value.code)


def test_root_inside_data_is_accepted(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    inbox = data_dir / "inbox"
    inbox.mkdir(parents=True)

    assert _ensure_root(inbox, data_dir) == inbox.resolve()


def test_iter_files_recurses_into_inbox_subdirs(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    (root / "manual").mkdir(parents=True)
    (root / "faq").mkdir(parents=True)
    (root / "manual" / "手册.pdf").write_bytes(b"%PDF-1.4")
    (root / "manual" / "笔记.txt").write_text("x", encoding="utf-8")
    (root / "faq" / "问答.xlsx").write_bytes(b"xlsx")

    names = [path.name for path in _iter_files(root)]

    assert sorted(names) == sorted(["手册.pdf", "笔记.txt", "问答.xlsx"])


def test_original_target_path_never_reuses_an_existing_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / ORIGINAL_DOCS_DIR).mkdir(parents=True)

    first = original_target_path(data_dir, "手册.pdf")
    first.write_bytes(b"first")
    second = original_target_path(data_dir, "手册.pdf")

    assert second != first
    assert second.name.endswith("-手册.pdf")
    assert not second.exists()


def test_office_lock_and_empty_files_are_skipped(tmp_path: Path) -> None:
    """扫描件目录里常有 Office 的 ~$ 锁文件与 0 字节文件，它们不该占文档编号。"""
    lock = tmp_path / "~$1-3_型式试验证书台账.xlsx"
    lock.write_bytes(b"")
    empty = tmp_path / "空表.xlsx"
    empty.write_bytes(b"")
    normal = tmp_path / "台账.xlsx"
    normal.write_bytes(b"xlsx")

    assert "锁文件" in (_skip_reason(lock) or "")
    assert "空文件" in (_skip_reason(empty) or "")
    assert _skip_reason(normal) is None


def test_excel_category_follows_staging_folder(tmp_path: Path) -> None:
    """`inbox/faq` 是 FAQ 表、`inbox/ticket` 是工单导出；`inbox/manual` 不指定，走默认值。"""
    data_dir = tmp_path / "data"
    faq = data_dir / "inbox" / "faq" / "问答.xlsx"
    ticket = data_dir / "inbox" / "ticket" / "工单导出.csv"
    manual = data_dir / "inbox" / "manual" / "宏远" / "1-3_型式试验证书台账.xlsx"
    for path in (faq, ticket, manual):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    assert _category_hint(faq, data_dir) == "faq"
    assert _category_hint(ticket, data_dir) == "ticket"
    assert _category_hint(manual, data_dir) is None


def test_excel_defaults_to_table_unless_named_faq_or_ticket() -> None:
    """表格类默认 `table`（台账/记录表），不再一律当 FAQ；显式分类优先。"""
    assert infer_metadata("1-3_型式试验证书台账.xlsx")["category"] == "table"
    assert infer_metadata("FAQ-制动器常见问题.xlsx")["category"] == "faq"
    assert infer_metadata("工单-2026.xlsx")["category"] == "ticket"
    assert infer_metadata("台帐.csv")["category"] == "table"
    assert infer_metadata("问答.xlsx", path_category="faq")["category"] == "faq"
    # 型号/版本对表格一律不推断（文件名里的编号不是型号）
    assert infer_metadata("1-3_型式试验证书台账.xlsx")["model"] is None
    assert infer_metadata("1-3_型式试验证书台账.xlsx")["version"] is None


def test_numeric_prefix_is_not_a_model() -> None:
    """文档编号（1-1_、3-1_、00_）不能当成适用型号写进 device_model。"""
    assert infer_metadata("3-1_电梯安全管理制度.docx")["model"] is None
    assert infer_metadata("00_品牌物料_宏远电梯.md")["model"] is None
    assert infer_metadata("1-2_电梯安装质量证明书（含自检报告）.docx")["model"] is None
    # 真型号仍要能推出来（含英文字母）
    assert infer_metadata("BK系列制动器维护保养手册Ver.3.pdf")["model"] == "BK系列"
