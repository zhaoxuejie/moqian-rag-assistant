"""解析与切片的中间数据结构。

拆成"解析"和"切片"两步：
- 解析只负责"从文件里取出带章节与页码的文本块"（机制，格式无关的抽象）；
- 切片负责"按证据单元粒度切分并编号"（机制）。
格式相关的细节（PDF 目录、Excel 行号、工单字段）在各自 parser 里，字段叫什么由 app 注入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Section:
    """一个章节（或一页）的文本块，带页码区间。

    `page_texts` 保留"每一页各自的文本"，切片时才能给每段标注**准确页码**，
    而不是整节共用一个页码范围（出处要可核验，不能糊）。
    """

    text: str
    page_from: int
    page_to: int
    heading: str | None = None
    level: int = 0
    """0 = 无标题（整页或整段）；1 = 章；2 = 节…"""
    page_texts: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class ParsedDocument:
    """解析结果。`title` 只在文件内自带标题时有值，缺省由调用方用元数据补。"""

    page_count: int
    sections: list[Section]
    title: str | None = None
    used_toc: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChunkDraft:
    """待入库的切片（尚未有证据 ID 与向量）。"""

    seq: int
    text: str
    loc: str | None
    page_from: int
    page_to: int
    section_no: str | None = None
    heading: str | None = None
    batch: str | None = None
    """该切片自带的批次覆盖值（FAQ 行级批次）；None = 继承文档级批次。
    值从哪一列来由调用方注入的列别名决定，机制层不写死行业词。"""
    meta: dict[str, Any] = field(default_factory=dict)
