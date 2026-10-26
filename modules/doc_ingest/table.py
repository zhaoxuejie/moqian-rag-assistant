"""通用表格解析（Excel / CSV）：一行一条记录 = 一个证据单元。

与 FAQ 解析的区别：FAQ 是"问答对"（问题+答案本身就是完整证据），通用表格是结构化
记录，表头是语义的一部分。所以这里把每行渲染成「列名：值」形式，检索命中后模型
才看得懂「0.4mm 是哪个参数的值」。

约定：
- 支持 .xlsx（多个工作表，逐表处理）与 .csv；
- 表头 = 前 10 行里**第一行有 ≥2 个非空单元格**的行（合并的标题行只有首格有值，不能当表头；
  整表只有一列时退回第一个非空行）；表头之前的标题/说明行单独成一条「前言」证据；
- 空表头列按「列1/列2…」占位，重名加序号；
- 空行跳过；单行文本超过上限截断（不静默丢行）；
- 出处是「工作表名 第N行」（CSV 用 `CSV`），`page_from/page_to` 置 0，不伪造页码；
- 行数超过 `max_rows` 直接拒绝并提示拆分（表格入库会产生大量切片，不能无限吃）；
- `chunk_rows > 1` 时把连续 K 行合并成一条证据（台账类低信息行），出处写「第a–b行」；
- `first_column_section=True` 时首列作为该条的章节/分组（如参数表的「型号」列）。
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from .types import ChunkDraft

logger = logging.getLogger(__name__)

_HEADER_SCAN_ROWS = 10
_MAX_TEXT_CHARS = 6000
_MAX_PLACEHOLDER_COLUMNS = 200
_CSV_SHEET = "CSV"


def _cell_text(value: Any) -> str:
    """单元格 → 文本：整数值的 float 不带 .0，None 当空。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_xlsx(path: str | Path) -> list[tuple[str, list[list[Any]]]]:
    import openpyxl  # 延迟导入：只有解析 Excel 时才需要

    workbook = openpyxl.load_workbook(filename=path, read_only=True, data_only=True)
    try:
        return [
            (sheet.title, [list(row) for row in sheet.iter_rows(values_only=True)])
            for sheet in workbook.worksheets
        ]
    finally:
        workbook.close()


def _read_csv(path: str | Path) -> list[tuple[str, list[list[Any]]]]:
    """CSV 按 UTF-8 读，失败退回 GBK（企业导出的 CSV 常见 GBK）。"""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            with Path(path).open("r", encoding=encoding, newline="") as handle:
                rows = [list(row) for row in csv.reader(handle)]
            return [(_CSV_SHEET, rows)]
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 编码无法识别（支持 UTF-8 / GBK），请另存为 UTF-8 后重试")


def _header_index(rows: list[list[Any]]) -> int | None:
    """表头位置：前 10 行里第一行有 ≥2 个非空单元格的行。

    企业台账常先有一两行合并标题（"宏远电梯 · 型式试验证书台账" + 依据/说明），
    read_only 读合并单元格时只有首格有值，按"第一个非空行"当表头会把标题当列名、
    把真正的表头当数据。只有整表一列（每行最多一个非空格）时才退回旧行为。
    """
    first_nonempty: int | None = None
    for index, row in enumerate(rows[:_HEADER_SCAN_ROWS]):
        filled = sum(1 for cell in row if _cell_text(cell))
        if not filled:
            continue
        if first_nonempty is None:
            first_nonempty = index
        if filled >= 2:
            return index
    return first_nonempty


def _preface(rows: list[list[Any]], header_index: int) -> tuple[int, int, str] | None:
    """表头之前的标题/说明行 → 一条独立证据（保留依据、适用范围，不并进表头）。"""
    lines: list[str] = []
    first_row = last_row = 0
    for index, row in enumerate(rows[:header_index]):
        text = " ".join(_cell_text(cell) for cell in row if _cell_text(cell))
        if not text:
            continue
        if first_row == 0:
            first_row = index + 1
        last_row = index + 1
        lines.append(text)
    if not lines:
        return None
    return first_row, last_row, "\n".join(lines)


def _header_names(header: list[Any]) -> list[str]:
    names: list[str] = []
    seen: dict[str, int] = {}
    for position, cell in enumerate(header[:_MAX_PLACEHOLDER_COLUMNS]):
        name = _cell_text(cell) or f"列{position + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}({seen[name]})"
        else:
            seen[name] = 1
        names.append(name)
    return names


def _row_text(header: list[str], row: list[Any]) -> str:
    parts: list[str] = []
    for position, name in enumerate(header):
        value = _cell_text(row[position]) if position < len(row) else ""
        if value:
            parts.append(f"{name}：{value}")
    return "；".join(parts)


def _loc(sheet: str, start: int, end: int) -> str:
    return f"{sheet} 第{start}行" if start == end else f"{sheet} 第{start}–{end}行"


def parse_table(
    path: str | Path,
    *,
    max_rows: int = 5000,
    chunk_rows: int = 1,
    first_column_section: bool = True,
) -> tuple[list[ChunkDraft], dict[str, Any]]:
    """解析通用表格：默认一行 → 一条 `ChunkDraft`；`chunk_rows>1` 时连续行合并成块。"""
    if chunk_rows < 1:
        raise ValueError("chunk_rows 必须 >= 1")
    suffix = Path(path).suffix.lower()
    if suffix == ".xlsx":
        sheets = _read_xlsx(path)
        file_format = "xlsx"
    elif suffix == ".csv":
        sheets = _read_csv(path)
        file_format = "csv"
    else:
        raise ValueError(f"通用表格只支持 .xlsx / .csv（当前 {suffix or '无后缀'}）")

    drafts: list[ChunkDraft] = []
    sheet_names: list[str] = []
    total_rows = 0
    for sheet_name, rows in sheets:
        header_index = _header_index(rows)
        if header_index is None:
            continue
        header = _header_names(rows[header_index])
        data_rows: list[tuple[int, str, str]] = []
        for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
            text = _row_text(header, row)
            if not text:
                continue
            if len(text) > _MAX_TEXT_CHARS:
                logger.warning(
                    "table row too long, truncated: %s 第%d行 (%d chars)",
                    sheet_name,
                    offset,
                    len(text),
                )
                text = text[:_MAX_TEXT_CHARS]
            first_cell = _cell_text(row[0]) if row else ""
            data_rows.append((offset, text, first_cell))
        if not data_rows:
            continue
        sheet_names.append(sheet_name)
        total_rows += len(data_rows)
        if total_rows > max_rows:
            raise ValueError(
                f"表格超过 {max_rows} 行，请拆分文件后再上传（参数表建议按型号/章节拆）"
            )
        preface = _preface(rows, header_index)
        if preface:
            first_row, last_row, text = preface
            drafts.append(
                ChunkDraft(
                    seq=len(drafts) + 1,
                    text=text,
                    loc=_loc(sheet_name, first_row, last_row),
                    page_from=0,
                    page_to=0,
                    section_no=None,
                    heading=sheet_name,
                    meta={
                        "sheet": sheet_name,
                        "row": first_row,
                        "rowEnd": last_row,
                        "preface": True,
                    },
                )
            )
        for start in range(0, len(data_rows), chunk_rows):
            block = data_rows[start : start + chunk_rows]
            text = "\n".join(item[1] for item in block)
            first_row, last_row = block[0][0], block[-1][0]
            drafts.append(
                ChunkDraft(
                    seq=len(drafts) + 1,
                    text=text,
                    loc=_loc(sheet_name, first_row, last_row),
                    page_from=0,
                    page_to=0,
                    section_no=(block[0][2] or None) if first_column_section else None,
                    heading=block[0][2] or sheet_name,
                    meta={
                        "sheet": sheet_name,
                        "row": first_row,
                        "rowEnd": last_row,
                    },
                )
            )

    if not drafts:
        raise ValueError("表格里没有可入库的数据行（需要一段表头 + 至少一行内容）")

    info: dict[str, Any] = {
        "rows": total_rows,
        "chunks": len(drafts),
        "sheets": sheet_names,
        "format": file_format,
        "chunkRows": chunk_rows,
    }
    return drafts, info
