"""PDF / Word / Markdown / FAQ Excel 解析与切片（离线用例，样本现场生成）。

验证的是"出处可核验"的前提：
- 章节与页码来自 PDF 书签，不是猜的；
- 切片的页码区间必须落在真实页上；
- 标题在切片正文里只出现一次（否则检索命中后展示会重复）；
- 抽不出文字的 PDF 要被识别为扫描件，而不是入库一堆空切片；
- Word / Markdown 没有页码，出处只能给章节（page 必须是 0，不能编）。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from modules.doc_ingest import (
    chunk_document,
    extract_section_no,
    is_probably_scanned,
    parse_docx,
    parse_faq_xlsx,
    parse_pdf,
    parse_plaintext,
)


def test_parse_pdf_uses_toc(sample_pdf) -> None:
    parsed = parse_pdf(sample_pdf)
    assert parsed.page_count == 3
    assert parsed.used_toc is True
    headings = [section.heading for section in parsed.sections]
    assert "6.4.2 主机轴承异响排查" in headings
    section = next(item for item in parsed.sections if item.heading == "6.4.2 主机轴承异响排查")
    assert (section.page_from, section.page_to) == (2, 2), "页码应取自书签，不是顺序号"
    assert "听音杆" in section.text
    assert section.page_texts and section.page_texts[0][0] == 2


def test_chunk_document_keeps_page_and_section(sample_pdf) -> None:
    parsed = parse_pdf(sample_pdf)
    chunks = chunk_document(parsed, max_chars=200, min_chars=20)
    assert chunks, "样本必须切出切片"

    bearing = next(chunk for chunk in chunks if chunk.section_no == "6.4.2")
    assert bearing.page_from == bearing.page_to == 2
    assert bearing.loc == "§6.4.2"
    assert "听音杆" in bearing.text

    gap = next(chunk for chunk in chunks if chunk.section_no == "6.4.3")
    assert (gap.page_from, gap.page_to) == (3, 3)
    assert "间隙" in gap.text


def test_chunk_text_contains_heading_once(sample_pdf) -> None:
    parsed = parse_pdf(sample_pdf)
    chunks = chunk_document(parsed, max_chars=200, min_chars=20)
    for chunk in chunks:
        heading = (chunk.heading or "").strip()
        if heading:
            assert chunk.text.count(heading) == 1, f"标题重复出现：{chunk.text[:60]!r}"


def test_chunk_respects_max_chars(sample_pdf) -> None:
    parsed = parse_pdf(sample_pdf)
    chunks = chunk_document(parsed, max_chars=80, min_chars=10)
    # 允许略超（标题 + 单句不可再切），但不能失控
    assert all(len(chunk.text) <= 80 * 2 for chunk in chunks)


def test_extract_section_no() -> None:
    assert extract_section_no("6.4.2 主机轴承异响排查") == "6.4.2"
    assert extract_section_no("第三章 制动器维护") == "第三章"
    assert extract_section_no("没有编号的标题") is None
    assert extract_section_no(None) is None


def test_scanned_pdf_detected(tmp_path) -> None:
    """没有文字层的 PDF（模拟扫描件）必须能被识别出来。"""
    path = tmp_path / "scanned.pdf"
    document = pymupdf.open()
    for _ in range(2):
        page = document.new_page()
        # 只画一条线，不放任何文字
        page.draw_line((72, 72), (200, 72))
    document.save(path)
    document.close()

    parsed = parse_pdf(path)
    assert parsed.page_count == 2
    assert is_probably_scanned(parsed) is True


def test_boilerplate_header_and_running_title_are_removed(tmp_path) -> None:
    """真实手册的坑：每页重复页眉 + 续页重复节标题，不能变成正文或新章节。"""
    path = tmp_path / "no-toc.pdf"
    document = pymupdf.open()
    for page_no, body in enumerate(
        [
            ("1 使用要则", "正确安装、使用和维护是安全运行的保证。"),
            ("1 使用要则", "维护作业前必须断电挂牌，并由持证人员执行。"),
            ("1.2 维护前的注意事项", "确认围挡、警示牌与作业空间到位。"),
        ],
        start=1,
    ):
        page = document.new_page()
        # 每页相同的页眉（带页码，签名应归一化）
        page.insert_text(
            (72, 50), f"BK 系列制动器维护保养手册   文件编号：3CA0141-12    {page_no}",
            fontname="china-s", fontsize=9,
        )
        page.insert_text((72, 90), body[0], fontname="china-s", fontsize=14)
        page.insert_text((72, 130), body[1], fontname="china-s", fontsize=11)
    document.save(path)
    document.close()

    parsed = parse_pdf(path)
    chunks = chunk_document(parsed, max_chars=500, min_chars=20)

    headings = [section.heading for section in parsed.sections if section.heading]
    assert headings.count("1 使用要则") == 1, "续页重复的节标题不能变成新章节"
    assert not any("文件编号" in chunk.text for chunk in chunks), "页眉不能进正文切片"
    assert not any("文件编号" in (chunk.heading or "") for chunk in chunks)

    section = next(item for item in parsed.sections if item.heading == "1 使用要则")
    assert section.page_from == 1 and section.page_to == 2, "一节跨页时页码区间应覆盖两页"
    assert "断电挂牌" in section.text


def _faq_xlsx(path, rows):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "FAQ"
    sheet.append(["分类", "问题", "答案", "关键词"])
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def test_parse_faq_xlsx_one_row_per_chunk(tmp_path) -> None:
    path = _faq_xlsx(
        tmp_path / "faq.xlsx",
        [
            ["间隙", "制动器间隙标准值是多少？", "按手册，BK 系列常见 0.4mm。", "间隙｜0.4"],
            ["异响", "运行异响怎么查？", "先查间隙与闸瓦。", "异响｜闸瓦"],
        ],
    )
    drafts, meta = parse_faq_xlsx(path)
    assert meta == {"rows": 2, "sheet": "FAQ"}
    assert [draft.seq for draft in drafts] == [1, 2]
    assert drafts[0].loc == "FAQ 第2行"
    assert drafts[0].page_from == drafts[0].page_to == 0
    assert drafts[0].text.startswith("问题："), "正文以问题开头，切片列表才先看到问题"
    assert "问题：制动器间隙标准值是多少？" in drafts[0].text
    assert drafts[0].text.endswith("分类：间隙"), "分类放末尾，仍保留在正文里"
    assert drafts[0].meta["keywords"] == ["间隙", "0.4"]
    assert drafts[1].heading == "异响"


def test_parse_faq_xlsx_rejects_missing_answer(tmp_path) -> None:
    path = _faq_xlsx(tmp_path / "bad.xlsx", [["间隙", "只有问题没有答案？", ""]])
    with pytest.raises(ValueError, match="第 2 行"):
        parse_faq_xlsx(path)


def test_parse_faq_xlsx_row_batch_column(tmp_path) -> None:
    """行级批次列：列名别名由 app 层注入；行内有值才落 batch，空值留 None。"""
    from openpyxl import Workbook

    from app.domain import FAQ_BATCH_COLUMN_ALIASES

    path = tmp_path / "faq-batch.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "FAQ"
    sheet.append(["问题", "答案", "适用批次"])
    sheet.append(["甲问题？", "甲答案。", "B22"])
    sheet.append(["乙问题？", "乙答案。", ""])
    workbook.save(path)

    drafts, _meta = parse_faq_xlsx(path, batch_keys=FAQ_BATCH_COLUMN_ALIASES)
    assert drafts[0].batch == "B22"
    assert drafts[1].batch is None, "空单元格不能变成空串批次"

    # 不传别名 = 旧行为：这一列当普通未知列忽略，不猜含义
    drafts_default, _meta = parse_faq_xlsx(path)
    assert all(draft.batch is None for draft in drafts_default)


def test_parse_table_xlsx_one_row_per_chunk(tmp_path) -> None:
    """通用表格：一行一条记录，正文渲染成「列名：值」，出处是工作表行号。"""
    from openpyxl import Workbook

    from modules.doc_ingest import parse_table

    path = tmp_path / "params.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "参数表"
    sheet.append(["型号", "间隙标准(mm)", "备注"])
    sheet.append(["BK", 0.4, "两侧均匀"])
    sheet.append(["DZS", 0.3, ""])
    sheet.append([None, None, None])  # 空行跳过
    workbook.save(path)

    drafts, info = parse_table(path)
    assert info["rows"] == 2
    assert info["chunks"] == 2
    assert info["sheets"] == ["参数表"]
    assert info["format"] == "xlsx"
    assert info["chunkRows"] == 1
    assert drafts[0].seq == 1
    assert drafts[0].loc == "参数表 第2行"
    assert drafts[0].page_from == drafts[0].page_to == 0
    assert drafts[0].section_no == "BK", "首列（型号）默认作为该条的章节/分组"
    assert "型号：BK" in drafts[0].text
    assert "间隙标准(mm)：0.4" in drafts[0].text, "整数值的 float 不能写成 0.4.0 或 0.0"
    assert "备注：两侧均匀" in drafts[0].text
    assert "型号：DZS" in drafts[1].text
    assert "备注" not in drafts[1].text, "空单元格不能渲染成「备注：」"


def test_parse_table_csv_and_row_limit(tmp_path) -> None:
    """CSV 同样走一行一条；超过行数上限要明确拒绝（避免一次灌爆检索池）。"""
    from modules.doc_ingest import parse_table

    path = tmp_path / "params.csv"
    path.write_text(
        "型号,间隙标准\nBK,0.4\nDZS,0.3\n", encoding="utf-8-sig"
    )
    drafts, info = parse_table(path)
    assert info["format"] == "csv"
    assert [draft.loc for draft in drafts] == ["CSV 第2行", "CSV 第3行"]
    assert "型号：BK" in drafts[0].text

    with pytest.raises(ValueError, match="超过 1 行"):
        parse_table(path, max_rows=1)


def test_parse_table_merge_rows_and_first_column_section(tmp_path) -> None:
    """台账类低信息行：连续 K 行合并成一条证据，出处给行范围；首列可关掉当章节。"""
    from openpyxl import Workbook

    from modules.doc_ingest import parse_table

    path = tmp_path / "bank.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "台账"
    sheet.append(["型号", "参数", "值"])
    sheet.append(["BK", "间隙", "0.4"])
    sheet.append(["BK", "力矩", "45"])
    sheet.append(["DZS", "间隙", "0.3"])
    workbook.save(path)

    drafts, info = parse_table(path, chunk_rows=2)
    assert info["rows"] == 3 and info["chunks"] == 2
    assert drafts[0].loc == "台账 第2–3行"
    assert drafts[0].text.count("\n") == 1, "合并块内每条记录一行，不能挤成一长串"
    assert drafts[0].section_no == "BK"
    assert drafts[1].loc == "台账 第4行"

    no_section, _info = parse_table(path, first_column_section=False)
    assert all(draft.section_no is None for draft in no_section)


def test_parse_table_rejects_empty_sheet(tmp_path) -> None:
    from openpyxl import Workbook

    from modules.doc_ingest import parse_table

    path = tmp_path / "empty.xlsx"
    workbook = Workbook()
    workbook.save(path)
    with pytest.raises(ValueError, match="没有可入库的数据行"):
        parse_table(path)


def test_parse_table_skips_merged_title_rows_for_header(tmp_path) -> None:
    """台账常见排版：合并标题 + 说明行在表头之前。

    标题行只有首格有值（合并单元格），不能当列名；它和说明行要单独成「前言」证据，
    真正的表头（第 3 行）才是列名，否则正文会退化成「列2：…」且表头变数据。
    """
    from openpyxl import Workbook

    from modules.doc_ingest import parse_table

    path = tmp_path / "ledger.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "型式试验证书台账"
    sheet.append(["宏远电梯 · 型式试验证书台账", "", ""])
    sheet.merge_cells("A1:C1")
    sheet.append(["依据《电梯监督检验和定期检验规则》填写。", "", ""])
    sheet.append(["序号", "部件名称", "证书编号"])
    sheet.append([1, "限速器", "TSG-T2026-001"])
    sheet.append([2, "安全钳", "TSG-T2026-002"])
    workbook.save(path)

    drafts, info = parse_table(path)
    assert info["rows"] == 2, "前言不算数据行"
    assert info["chunks"] == 3, "2 行数据 + 1 条前言"
    assert drafts[0].loc == "型式试验证书台账 第1–2行"
    assert "宏远电梯 · 型式试验证书台账" in drafts[0].text
    assert "依据" in drafts[0].text
    assert drafts[0].section_no is None
    assert drafts[1].loc == "型式试验证书台账 第4行"
    assert "部件名称：限速器" in drafts[1].text
    assert "证书编号：TSG-T2026-001" in drafts[1].text
    assert "列2" not in drafts[1].text, "真正的表头列名必须生效"


def test_shipped_faq_template_matches_parser() -> None:
    """对外下载的 FAQ 模板必须和解析器对齐：列名一致、能解析、适用批次列被识别。

    模板是二进制资产，改列名或改解析器都容易忘记另一边；这条用例把它钉住。
    """
    from openpyxl import load_workbook

    from app.domain import FAQ_BATCH_COLUMN_ALIASES

    template = (
        Path(__file__).resolve().parent.parent
        / "frontend"
        / "admin"
        / "assets"
        / "faq-import-template.xlsx"
    )
    assert template.is_file(), "后台缺少 FAQ 模板（make_faq.py --template 生成）"
    workbook = load_workbook(template, read_only=True)
    sheet = workbook["FAQ"]
    header = next(sheet.iter_rows(values_only=True))
    workbook.close()
    assert list(header) == ["分类", "问题", "答案", "关键词", "适用批次"]

    drafts, meta = parse_faq_xlsx(template, batch_keys=FAQ_BATCH_COLUMN_ALIASES)
    assert meta["rows"] == 2
    assert drafts[0].batch == "B22"
    assert drafts[1].batch is None, "空批次行要继承文档级批次，不能变成空串"


def test_parse_markdown_sections_have_no_page(tmp_path) -> None:
    """Markdown 按标题分节，出处只能是章节；页码必须是 0（不伪造）。"""
    path = tmp_path / "guide.md"
    path.write_text(
        "# 制动器维护\n\n"
        "本手册适用于 BK-PM* 与 BK-FM* 系列。\n\n"
        "## 3.1 间隙检查\n\n"
        "断电挂牌后测量间隙，数值以铭牌型号对应手册为准。\n",
        encoding="utf-8",
    )
    parsed = parse_plaintext(path)
    assert [section.heading for section in parsed.sections] == ["制动器维护", "3.1 间隙检查"]
    assert all(section.page_from == 0 for section in parsed.sections)

    drafts = chunk_document(parsed, max_chars=200, min_chars=20)
    assert drafts, "Markdown 也要能切片入库"
    assert all(draft.page_from == 0 and draft.page_to == 0 for draft in drafts)
    numbered = next(draft for draft in drafts if "间隙" in draft.text)
    assert numbered.section_no == "3.1"
    assert numbered.loc == "§3.1"


def test_parse_txt_is_single_section(tmp_path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text(
        "第一段：制动器保养周期见表。\n\n第二段：间隙调节按手册执行。\n", encoding="utf-8"
    )
    parsed = parse_plaintext(path)
    assert len(parsed.sections) == 1
    assert parsed.page_count == 0
    drafts = chunk_document(parsed, max_chars=300, min_chars=20)
    assert drafts and all(draft.page_from == 0 for draft in drafts)


def _docx_file(path, *, with_table: bool = True):
    from docx import Document

    document = Document()
    document.add_paragraph("BK 系列制动器维护保养手册", style="Heading 1")
    document.add_paragraph("本手册适用于 BK-PM* 与 BK-FM* 系列制动器，安装与维保须由持证人员执行。")
    document.add_paragraph("3.1 制动间隙调节")
    document.add_paragraph("断电挂牌后按手册测量间隙，调整后复核制动力矩，确认开闸顺畅再恢复运行。")
    if with_table:
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "项目"
        table.cell(0, 1).text = "要求"
        table.cell(1, 0).text = "间隙"
        table.cell(1, 1).text = "以对应型号手册为准"
    document.save(str(path))
    return path


def test_parse_docx_headings_and_table(tmp_path) -> None:
    """Word 按标题样式/编号分节，表格按行并入正文，页码一律为 0。"""
    path = _docx_file(tmp_path / "manual.docx")
    parsed = parse_docx(path)
    headings = [section.heading for section in parsed.sections]
    assert "BK 系列制动器维护保养手册" in headings
    assert "3.1 制动间隙调节" in headings, "编号短行也要识别成标题"
    assert parsed.page_count == 0
    table_section = next(
        section for section in parsed.sections if "以对应型号手册为准" in section.text
    )
    assert "间隙 ｜ 以对应型号手册为准" in table_section.text

    drafts = chunk_document(parsed, max_chars=200, min_chars=20)
    assert all(draft.page_from == 0 and draft.page_to == 0 for draft in drafts)
    assert any(draft.section_no == "3.1" for draft in drafts)
    assert any("制动器维护保养手册" in draft.text for draft in drafts)


def test_docx_text_lines_marks_headings(tmp_path) -> None:
    """原文预览要能看出哪行是标题（标题前加 ##）。"""
    from modules.doc_ingest.docx import docx_text_lines

    path = _docx_file(tmp_path / "preview.docx")
    lines = docx_text_lines(path)
    assert lines[0] == "## BK 系列制动器维护保养手册"
    assert "## 3.1 制动间隙调节" in lines
    assert "项目 ｜ 要求" in lines
