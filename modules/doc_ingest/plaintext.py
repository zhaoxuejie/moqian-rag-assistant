"""Markdown / 纯文本解析：按标题切章节，没有页码（出处用章节标题）。

- `.md`：`#`~`######` 标题切节，标题下的正文归该节；
- `.txt`：没有标题语法，整篇当作一节，切片器仍按段落切；
- 不解析花哨语法（表格、内嵌 HTML、图片），保留原文——工程师看的就是原文，
  改写反而会把要核对的内容弄丢。

Word（.docx）另有 `docx.parse_docx`；两者都产出 `ParsedDocument`，
`page_from/page_to` 一律为 0：这两种格式没有可靠分页，
出处只能说清"哪一节"，不能伪造页码。
"""

from __future__ import annotations

import re
from pathlib import Path

from .types import ParsedDocument, Section

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MAX_BYTES = 8 * 1024 * 1024
_MARKDOWN_SUFFIXES = {".md", ".markdown"}


def _read_text(path: Path) -> str:
    raw = path.read_bytes()[:_MAX_BYTES]
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # 老资料常是 GBK 导出的 .txt；解不出来就替换，别整份失败
        return raw.decode("gb18030", errors="replace")


def parse_plaintext(path: str | Path) -> ParsedDocument:
    """Markdown / TXT → 按标题分节（无标题时整篇一节）。"""
    target = Path(path)
    is_markdown = target.suffix.lower() in _MARKDOWN_SUFFIXES
    text = _read_text(target).replace("\r\n", "\n").replace("\r", "\n")

    sections: list[Section] = []
    heading: str | None = None
    level = 0
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body or heading:
            sections.append(
                Section(
                    text=body,
                    page_from=0,
                    page_to=0,
                    heading=heading,
                    level=level,
                )
            )
        buffer.clear()

    for line in text.split("\n"):
        match = _HEADING.match(line) if is_markdown else None
        if match:
            flush()
            level = len(match.group(1))
            heading = match.group(2).strip()
            continue
        buffer.append(line)
    flush()

    sections = [item for item in sections if item.text.strip() or (item.heading or "").strip()]
    if not sections:
        sections = [Section(text=text.strip(), page_from=0, page_to=0)]
    title = next((item.heading for item in sections if item.heading), target.stem)
    return ParsedDocument(
        page_count=0,
        sections=sections,
        title=title,
        used_toc=False,
        meta={"format": "markdown" if is_markdown else "text"},
    )
