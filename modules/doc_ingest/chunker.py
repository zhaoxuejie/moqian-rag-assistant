"""切片（证据单元）切分：把章节文本切成可检索、可核验出处的块。

三条约束来自产品纪律：
1. **原子是切片，不是文件**（architecture §5.1）：每块都要能单独作为证据被引用；
2. **出处随格式走**：PDF 的出处是页码，所以每块必须带准确页码（用 `page_texts` 逐页归属，不估算）；
3. **不做无依据的合并**：小块宁可单独成块，也不和别的章节拼在一起（否则引用会指错地方）。

切分策略：
- 段落优先（空行 / 换行结构），单段过长时按句号硬切；
- 每块正文以章节标题行开头（检索命中时模型能看到"这是哪一节"），已含标题则不重复；
- 短块（< min_chars）并回前一块，但仅限同一章节内。
"""

from __future__ import annotations

import re

from .pdf import extract_section_no
from .types import ChunkDraft, ParsedDocument, Section

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;])")
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")


def chunk_document(
    parsed: ParsedDocument,
    *,
    max_chars: int = 700,
    min_chars: int = 120,
    include_heading: bool = True,
) -> list[ChunkDraft]:
    """把解析结果切成 ChunkDraft 列表（seq 从 1 连续编号）。"""
    drafts: list[ChunkDraft] = []

    for section in parsed.sections:
        section_no = extract_section_no(section.heading)
        heading = (section.heading or "").strip()
        paragraphs = _section_paragraphs(section)
        # 章节正文通常以标题行开头；标题由切片统一补，正文里先去掉，避免每块重复两遍
        if heading and paragraphs and paragraphs[0][1].strip() == heading:
            paragraphs = paragraphs[1:]
        buffer: list[str] = []
        buffer_pages: list[int] = []

        for page_no, paragraph in paragraphs:
            heading_len = len(heading)
            candidate_len = sum(len(item) for item in buffer) + len(paragraph)
            if buffer and candidate_len + heading_len > max_chars:
                _flush(drafts, buffer, buffer_pages, section, section_no)
            if not buffer and include_heading and heading:
                buffer.append(heading)
                buffer_pages.append(page_no)
            budget = max_chars - (heading_len if len(buffer) > 1 else 0)
            for piece in _split_long(paragraph, budget):
                if buffer and sum(len(item) for item in buffer) + len(piece) > max_chars:
                    _flush(drafts, buffer, buffer_pages, section, section_no)
                buffer.append(piece)
                buffer_pages.append(page_no)
        _flush(drafts, buffer, buffer_pages, section, section_no)

    return _merge_small(drafts, min_chars=min_chars)


def _flush(
    drafts: list[ChunkDraft],
    buffer: list[str],
    buffer_pages: list[int],
    section: Section,
    section_no: str | None,
) -> None:
    """把缓冲区落成一个切片，并清空缓冲（原地清空，避免闭包捕获循环变量）。"""
    text = "\n".join(buffer).strip()
    if text:
        drafts.append(
            ChunkDraft(
                seq=len(drafts) + 1,
                text=text,
                loc=_loc(section_no, section.heading),
                page_from=min(buffer_pages) if buffer_pages else section.page_from,
                page_to=max(buffer_pages) if buffer_pages else section.page_to,
                section_no=section_no,
                heading=section.heading,
                meta={"heading": section.heading} if section.heading else {},
            )
        )
    buffer.clear()
    buffer_pages.clear()


def _section_paragraphs(section: Section) -> list[tuple[int, str]]:
    """章节 → [(页码, 段落)]。优先用逐页文本，保证页码准确。"""
    if section.page_texts:
        items: list[tuple[int, str]] = []
        for page_no, page_text in section.page_texts:
            for paragraph in _paragraphs(page_text):
                items.append((page_no, paragraph))
        return items
    return [(section.page_from, paragraph) for paragraph in _paragraphs(section.text)]


def _paragraphs(text: str) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text)]
    result: list[str] = []
    for block in blocks:
        if not block:
            continue
        for line in block.splitlines():
            line = line.strip()
            if line:
                result.append(line)
    return result


def _split_long(paragraph: str, budget: int) -> list[str]:
    """单段超过预算时按句切；仍超长就硬切（中文没有空格，不能按词切）。"""
    budget = max(200, budget)
    if len(paragraph) <= budget:
        return [paragraph]
    sentences = [item for item in _SENTENCE_SPLIT.split(paragraph) if item]
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > budget:
            pieces.append(current)
            current = ""
        while len(sentence) > budget:
            pieces.append(sentence[:budget])
            sentence = sentence[budget:]
        current += sentence
    if current:
        pieces.append(current)
    return pieces


def _starts_with(buffer: list[str]) -> bool:
    """缓冲区首行是否已经是标题（避免每块重复标题）。"""
    return bool(buffer)


def _loc(section_no: str | None, heading: str | None) -> str | None:
    if section_no:
        return f"§{section_no}"
    return heading


def _merge_small(drafts: list[ChunkDraft], *, min_chars: int) -> list[ChunkDraft]:
    """把过短的块并回**同一章节**的前一块；跨章节不合并（合并会让引用指错节）。"""
    merged: list[ChunkDraft] = []
    for draft in drafts:
        if (
            merged
            and len(draft.text) < min_chars
            and merged[-1].section_no == draft.section_no
            and merged[-1].heading == draft.heading
            and len(merged[-1].text) + len(draft.text) <= min_chars * 3
        ):
            previous = merged[-1]
            previous.text = previous.text + "\n" + draft.text
            previous.page_from = min(previous.page_from, draft.page_from)
            previous.page_to = max(previous.page_to, draft.page_to)
            continue
        merged.append(draft)
    for index, draft in enumerate(merged, start=1):
        draft.seq = index
    return merged


def is_probably_scanned(parsed: ParsedDocument) -> bool:
    """粗略判断是否扫描件：整份几乎没有可提取汉字就提示需要 OCR。"""
    text = "".join(section.text for section in parsed.sections)
    cjk = len(_HAS_CJK.findall(text))
    return cjk < max(20, parsed.page_count * 5)
