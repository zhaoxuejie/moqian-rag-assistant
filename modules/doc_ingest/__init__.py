"""doc_ingest：解析 → 切片（→ 向量化由 app 编排，向量客户端在 modules/llm）。S2 落地。

分层：
- `pdf.parse_pdf`：PDF → 带章节与逐页文本的 `ParsedDocument`（格式相关，机制通用）；
- `docx.parse_docx`：Word → 按标题样式/编号分节，表格逐行并入正文（无页码）；
- `plaintext.parse_plaintext`：Markdown / TXT → 按 `#` 标题或整篇分节（无页码）；
- `excel.parse_faq_xlsx`：Excel FAQ → 逐行 `ChunkDraft`（问答本身即证据单元，不再切）；
- `table.parse_table`：通用表格（Excel / CSV）→ 逐行 `ChunkDraft`（正文按「列名：值」渲染）；
- `chunker.chunk_document`：`ParsedDocument` → `ChunkDraft`（按证据单元粒度切、带准确页码）；
- 证据 ID、落库、向量化批次由 `app` 编排（ID 前缀属行业词汇：M/T/F/D…）。

出处随格式走：PDF 有页码，Word / Markdown / TXT 只有章节，Excel / CSV 是工作表行号。
"""

from .chunker import chunk_document, is_probably_scanned
from .docx import parse_docx
from .excel import parse_faq_xlsx
from .pdf import extract_section_no, parse_pdf
from .plaintext import parse_plaintext
from .table import parse_table
from .types import ChunkDraft, ParsedDocument, Section

__all__ = [
    "ChunkDraft",
    "ParsedDocument",
    "Section",
    "chunk_document",
    "extract_section_no",
    "is_probably_scanned",
    "parse_docx",
    "parse_faq_xlsx",
    "parse_pdf",
    "parse_plaintext",
    "parse_table",
]
