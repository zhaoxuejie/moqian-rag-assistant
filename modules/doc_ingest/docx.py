"""Word（.docx）解析：按标题样式/编号切章节，出处用章节标题（Word 无稳定页码）。

- 标题识别：样式名 `Heading 1-3` / `标题 1-3`；或短行且以 `1`、`1.2` 这类编号
  开头、结尾没有句号（避免把"1. 检查间隙 0.4mm…"这种正文当标题）；
- 表格按「单元格 ｜ 单元格」逐行展开，保持文档顺序——Word 里表格常是参数表，
  丢掉就等于把证据丢了；
- 不读页眉页脚、批注与修订痕迹：页眉页脚多为重复的公司名/文件名，
  与 PDF 的"运行标题去重"口径一致。

`page_from/page_to` 一律为 0：Word 的分页由渲染器决定，落库时不能凭字数估算，
否则引用会指到不存在的页码。
"""

from __future__ import annotations

import re
from pathlib import Path

from .types import ParsedDocument, Section

_HEADING_STYLE = re.compile(r"^(?:heading|标题)\s*([1-6])$", re.IGNORECASE)
_NUMBERED = re.compile(r"^\d+(?:\.\d+)*[\s、.．]\s*\S")
_TERMINAL = ("。", "；", ";", "，", ",", "：", ":")
_MAX_HEADING_CHARS = 48
_MAX_TABLE_COLUMNS = 12


def _style_level(style_name: str | None) -> int:
    match = _HEADING_STYLE.match((style_name or "").strip())
    return int(match.group(1)) if match else 0


def _looks_like_numbered_heading(text: str) -> bool:
    if not text or len(text) > _MAX_HEADING_CHARS:
        return False
    if text.endswith(_TERMINAL):
        return False
    return bool(_NUMBERED.match(text))


def _iter_blocks(document):  # type: ignore[no-untyped-def]
    """按文档顺序产出段落与表格（python-docx 的 paragraphs/tables 是分列表的）。"""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def _table_lines(table) -> list[str]:  # type: ignore[no-untyped-def]
    lines: list[str] = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells[:_MAX_TABLE_COLUMNS]]
        cells = [cell for cell in cells if cell]
        if cells:
            lines.append(" ｜ ".join(cells))
    return lines


def docx_text_lines(path: str | Path) -> list[str]:
    """Word → 纯文本行：标题前加 `##`、表格按行展开（原文预览用，保留文档顺序）。"""
    from docx import Document

    document = Document(str(Path(path)))
    lines: list[str] = []
    for block in _iter_blocks(document):
        if hasattr(block, "rows"):
            lines.extend(_table_lines(block))
            continue
        text = (block.text or "").strip()
        if not text:
            continue
        if _style_level(getattr(block.style, "name", None)) or _looks_like_numbered_heading(text):
            lines.append(f"## {text}")
        else:
            lines.append(text)
    return lines


def parse_docx(path: str | Path) -> ParsedDocument:
    """Word → 按标题分节（无标题时整篇一节），表格逐行并入正文。"""
    from docx import Document

    target = Path(path)
    document = Document(str(target))

    sections: list[Section] = []
    heading: str | None = None
    level = 0
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body or heading:
            sections.append(
                Section(text=body, page_from=0, page_to=0, heading=heading, level=level)
            )
        buffer.clear()

    for block in _iter_blocks(document):
        if hasattr(block, "rows"):  # 表格
            buffer.extend(_table_lines(block))
            continue
        text = (block.text or "").strip()
        if not text:
            continue
        style_level = _style_level(getattr(block.style, "name", None))
        if style_level or _looks_like_numbered_heading(text):
            flush()
            level = style_level or 2
            heading = text
            continue
        buffer.append(text)
    flush()

    sections = [item for item in sections if item.text.strip() or (item.heading or "").strip()]
    if not sections:
        sections = [Section(text="", page_from=0, page_to=0)]
    title = (document.core_properties.title or "").strip() or next(
        (item.heading for item in sections if item.heading), target.stem
    )
    return ParsedDocument(
        page_count=0,
        sections=sections,
        title=title,
        used_toc=False,
        meta={"format": "docx"},
    )
