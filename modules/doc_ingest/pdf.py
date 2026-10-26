"""PDF 解析：优先用书签目录（TOC），没有目录时按页 + 标题样式兜底。

为什么优先 TOC：手册的目录就是"章节 → 页码"的权威映射，比从字体大小猜标题可靠；
兜底路径只在无目录 PDF 上使用，识别不到的标题就退化为"整页一个块"，不硬造章节号
（纪律 2 的同类原则：不知道就不写）。

三类噪声必须清掉，否则"出处可核验"会被污染：
1. **页眉/页脚**（书名、文件编号、版本号）：多页重复出现 → 按跨页频率识别后删除；
2. **目录行**（点线引导符 + 页码）：不是正文标题，也绝不能当章节；
3. **页码、热线、网址**：不承载知识，且会误导章节识别。

页码一律 **1-based**（与 PDF 阅读器一致），写进切片后原样展示给工程师。
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .types import ParsedDocument, Section

# 标题样式兜底：只认这几种明显形态，宁可漏认也不误认
_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^第[一二三四五六七八九十百零〇]+章[\s：:、.．]*(.*)$"), 1),
    (re.compile(r"^第[一二三四五六七八九十百零〇]+节[\s：:、.．]*(.*)$"), 2),
    # 编号标题：数字后必须是中文（`24 小时服务热线` 这类会被电话/热线规则挡掉）
    (re.compile(r"^\d+(?:\.\d+){0,3}\s+[\u4e00-\u9fff].*$"), 2),
    (re.compile(r"^附录\s*[A-Z一二三四五六七八九十]?[\s：:、.．]*(.*)$"), 1),
)

# 热线/电话/网址行：常以数字开头，误判成标题会破坏章节结构
_PHONE_LIKE = re.compile(r"(\d{3,}[-－]\d{3,}|\d{7,}|https?://|www\.)")

_SECTION_NO = re.compile(r"^(\d+(?:\.\d+){0,3})")
_CHAPTER_NO = re.compile(r"^第([一二三四五六七八九十百零〇]+)([章节])")
_PAGE_NOISE = re.compile(r"^[\-—–\s]*\d{1,4}[\-—–\s]*$")

# 目录行：点线引导符（····· / ...... / ……）后跟页码
_TOC_LINE = re.compile(r"(?:[·•]\s*){3,}|\.{4,}|…{2,}")
_HEADING_TRAILING_NOISE = re.compile(r"[\s·•.…]+\d{1,4}\s*$")


def _norm(line: str) -> str:
    return re.sub(r"\s+", "", line)


def _signature(line: str) -> str:
    """页眉归一化：去掉行尾页码后再比较（`文件编号… 版本：3   1` 与 `…2` 视为同一行）。"""
    return re.sub(r"\d+$", "", _norm(line))


def _is_toc_line(line: str) -> bool:
    return bool(_TOC_LINE.search(line))


def _clean_heading(line: str) -> str:
    """去掉标题尾部的点线与页码（`5 制动机构… ····· 10` → `5 制动机构…`）。"""
    return _HEADING_TRAILING_NOISE.sub("", line).strip()


def _clean_lines(text: str) -> list[str]:
    """逐行清洗：去纯页码、目录行，保留段落（空行保留用于分段）。"""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
            continue
        if _PAGE_NOISE.match(line):  # 纯页码
            continue
        if _is_toc_line(line):  # 目录条目（点线引导符）
            continue
        lines.append(line)
    return lines


def _boilerplate_lines(page_lines: list[list[str]]) -> set[str]:
    """跨页重复出现的短行 = 页眉/页脚，返回其"签名"集合。"""
    if len(page_lines) < 2:
        return set()
    threshold = max(2, math.ceil(len(page_lines) * 0.5))
    counts: dict[str, int] = {}
    heading_sigs: set[str] = set()
    for lines in page_lines:
        for line in lines:
            if not line:
                continue
            key = _signature(line)
            counts[key] = counts.get(key, 0) + 1
            if _detect_heading(line) is not None:
                heading_sigs.add(key)
    return {
        key
        for key, count in counts.items()
        # 标题样式的行不参与"页眉"判定：它可能既是真标题、又是续页页眉，
        # 续页重复由 `_sections_by_heading` 的"同标题跳过"处理，不能在这里一刀切
        if count >= threshold and 4 <= len(key) <= 80 and key not in heading_sigs
    }


def _detect_heading(line: str) -> tuple[str, int] | None:
    if _is_toc_line(line):  # 目录行永远不当标题
        return None
    if _PHONE_LIKE.search(line):  # 热线/网址行不是标题
        return None
    if line.strip() in {"目录", "目 录", "Contents"}:
        return None
    for pattern, level in _HEADING_PATTERNS:
        if pattern.match(line):
            cleaned = _clean_heading(line)
            if len(cleaned) > 48:  # 过长的多半是正文句子误命中
                return None
            return cleaned, level
    return None


def parse_pdf(path: str | Path) -> ParsedDocument:
    """解析 PDF。返回带章节/页码的文本块，不负责切片。"""
    import pymupdf  # 延迟导入：只有解析任务需要它

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF 不存在：{path}")

    with pymupdf.open(path) as doc:
        page_count = doc.page_count
        page_lines = [
            _clean_lines(doc.load_page(index).get_text("text")) for index in range(page_count)
        ]
        boilerplate = _boilerplate_lines(page_lines)
        if boilerplate:
            page_lines = [
                [line for line in lines if not (line and _signature(line) in boilerplate)]
                for lines in page_lines
            ]

        toc = doc.get_toc(simple=True) or []
        sections = (
            _sections_from_toc(page_lines, toc, page_count)
            if toc
            else _sections_by_heading(page_lines)
        )
        used_toc = bool(toc) and bool(sections)
        if toc and not sections:  # 目录有书签但取不到正文，退回按页
            sections = _sections_by_heading(page_lines)
            used_toc = False

        meta: dict = {"boilerplateLines": len(boilerplate)}
        raw_meta = doc.metadata or {}
        if raw_meta.get("title"):
            meta["embedded_title"] = raw_meta["title"]

    sections = [section for section in sections if section.text.strip()]
    return ParsedDocument(
        page_count=page_count,
        sections=sections,
        title=meta.get("embedded_title"),
        used_toc=used_toc,
        meta=meta,
    )


# ---------- TOC 路径 ----------
def _sections_from_toc(
    page_lines: list[list[str]], toc: list, page_count: int
) -> list[Section]:
    """TOC → 章节：每个书签的正文范围是 [本条页码, 下一条页码)。"""
    entries = [(int(item[0]), str(item[1]).strip(), int(item[2])) for item in toc]
    entries = [(level, title, page) for level, title, page in entries if page >= 1]
    sections: list[Section] = []
    for index, (level, title, page) in enumerate(entries):
        next_page = entries[index + 1][2] if index + 1 < len(entries) else page_count + 1
        end_page = max(page, min(next_page - 1, page_count)) if next_page > page else page
        page_texts = _page_texts(page_lines, page, end_page)
        if index + 1 < len(entries):
            next_level, next_title, next_entry_page = entries[index + 1]
            if next_entry_page == page:
                # 同页的父子书签：本段正文截止到下一个标题出现处，避免父子内容重复
                page_texts = _truncate_at_heading(page_texts, next_title)
        page_texts = _drop_own_heading(page_texts, title)
        if not page_texts:
            continue
        sections.append(
            Section(
                text="\n".join(text for _, text in page_texts),
                page_from=page,
                page_to=end_page,
                heading=title,
                level=level,
                page_texts=page_texts,
            )
        )
    return sections


def _drop_own_heading(
    page_texts: list[tuple[int, str]], title: str
) -> list[tuple[int, str]]:
    """章节正文开头的标题行去掉（切片会统一补标题，避免重复）。"""
    marker = _norm(title)
    if not marker:
        return page_texts
    result: list[tuple[int, str]] = []
    dropped = False
    for page_no, text in page_texts:
        if not dropped:
            lines = text.splitlines()
            if lines and _norm(lines[0]) == marker:
                dropped = True
                text = "\n".join(lines[1:]).strip()
                if not text:
                    continue
        result.append((page_no, text))
    return result


def _truncate_at_heading(
    page_texts: list[tuple[int, str]], next_title: str
) -> list[tuple[int, str]]:
    """把章节正文截断到"下一个同级标题出现处"（仅用于同页父子书签）。"""
    marker = _norm(next_title)
    if not marker:
        return page_texts
    result: list[tuple[int, str]] = []
    for page_no, text in page_texts:
        position = _norm(text).find(marker)
        if position == 0:
            break
        if position > 0:
            # 规范化串里的偏移与原文不一致，按行找更可靠
            lines = text.splitlines()
            cut = next(
                (index for index, line in enumerate(lines) if _norm(line) == marker), None
            )
            if cut is not None:
                kept = "\n".join(lines[:cut]).strip()
                if kept:
                    result.append((page_no, kept))
                break
        result.append((page_no, text))
    return [(page_no, text) for page_no, text in result if text.strip()]


# ---------- 无目录兜底 ----------
def _sections_by_heading(page_lines: list[list[str]]) -> list[Section]:
    """按页取文本，页内按标题样式切块；识别不到标题就整页一块。

    关键细节：**不要每页末尾都 flush**。一节跨多页时如果按页切，会出现
    "同一标题重复成多节 + 每节都带整页文本"的重复；只在遇到新标题或文末时 flush。
    没有标题的章节（文档开头、无结构页）才按页切，保证兜底也能用。
    """
    sections: list[Section] = []
    current_heading: str | None = None
    current_level = 0
    buffer: list[str] = []
    page_buffer: list[tuple[int, list[str]]] = []

    def start_page(page_no: int) -> None:
        if not page_buffer or page_buffer[-1][0] != page_no:
            page_buffer.append((page_no, []))

    def flush(page_to: int) -> None:
        nonlocal buffer, page_buffer, current_heading, current_level
        text = "\n".join(buffer).strip()
        if text:
            page_texts = [
                (page_no, "\n".join(lines).strip())
                for page_no, lines in page_buffer
                if "\n".join(lines).strip()
            ]
            sections.append(
                Section(
                    text=text,
                    page_from=page_texts[0][0] if page_texts else page_to,
                    page_to=page_texts[-1][0] if page_texts else page_to,
                    heading=current_heading,
                    level=current_level,
                    page_texts=page_texts,
                )
            )
        buffer = []
        page_buffer = []

    for page_no, lines in enumerate(page_lines, start=1):
        start_page(page_no)
        for line in lines:
            if not line:
                buffer.append("")
                continue
            detected = _detect_heading(line)
            if detected and detected[0] == current_heading:
                # 续页页眉（重复上一节标题）不是新章节，也不进正文
                continue
            if detected:
                flush(page_no)
                current_heading, current_level = detected
                start_page(page_no)
            buffer.append(line)
            page_buffer[-1][1].append(line)
        # 没有标题的兜底区：按页切，避免整本文档挤成一块
        if current_heading is None:
            flush(page_no)
    flush(len(page_lines))
    return sections


def _page_texts(
    page_lines: list[list[str]], start: int, end: int
) -> list[tuple[int, str]]:
    """取 [start, end] 闭区间内**每页**的非空文本（1-based）。"""
    items: list[tuple[int, str]] = []
    for page_no in range(max(1, start), min(end, len(page_lines)) + 1):
        text = "\n".join(page_lines[page_no - 1]).strip()
        if text:
            items.append((page_no, text))
    return items


def extract_section_no(heading: str | None) -> str | None:
    """从标题里抽章节号：`6.4.2 主机轴承异响排查` → `6.4.2`；`第三章 …` → `第三章`。

    抽不到就返回 None —— 宁可不写，也不编一个号（出处必须可核验）。
    """
    if not heading:
        return None
    text = heading.strip()
    match = _SECTION_NO.match(text)
    if match:
        return match.group(1)
    match = _CHAPTER_NO.match(text)
    if match:
        return f"第{match.group(1)}{match.group(2)}"
    return None
