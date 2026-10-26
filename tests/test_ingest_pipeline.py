"""入库流水线（需要数据库）：建档 → 解析 → 切片 → 向量化 → 落库 → 幂等重跑。

用假 embedding（1024 维确定性向量）避免依赖本机 Ollama；要验证的真模型连通
由 `app.import_docs --parse-now` 在本机跑。
"""

from __future__ import annotations

import shutil

import pytest
from conftest import requires_db

from app.config import Settings
from app.ingest import (
    IngestError,
    evidence_id_for,
    parse_document,
    resolve_document_path,
    upsert_document,
)

pytestmark = requires_db


@pytest.fixture()
def ingest_env(conn, tmp_path, sample_pdf):
    """临时 data 目录 + 一份已建档的 PDF；用完删掉文档（级联删切片）。"""
    docs_dir = tmp_path / "original-docs"
    docs_dir.mkdir()
    target = docs_dir / "BK-series-brake-manual.pdf"
    shutil.copyfile(sample_pdf, target)

    settings = Settings(
        data_dir=str(tmp_path),
        doc_ingest_chunk_max_chars=200,
        doc_ingest_chunk_min_chars=20,
    )
    document_id, created = upsert_document(
        conn,
        title="BK 系列制动器维护保养手册",
        file_path="original-docs/BK-series-brake-manual.pdf",
        source_type="pdf",
        sha256="unit-test-sha256",
        meta={"category": "manual", "model": "BK系列", "version": "Ver.3"},
    )
    assert created is True
    yield settings, document_id
    with conn.cursor() as cur:
        cur.execute("delete from documents where id = %s", (document_id,))
    conn.commit()


def test_faq_row_batch_overrides_document_batch(conn, tmp_path, fake_embedder) -> None:
    """FAQ「适用批次」列是行级批次：行内有值优先，空行继承文档级批次。"""
    from openpyxl import Workbook

    docs_dir = tmp_path / "original-docs"
    docs_dir.mkdir()
    target = docs_dir / "faq-row-batch.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "FAQ"
    sheet.append(["问题", "答案", "适用批次"])
    sheet.append(["甲问题？", "甲答案。", "B22"])
    sheet.append(["乙问题？", "乙答案。", ""])
    workbook.save(target)

    settings = Settings(data_dir=str(tmp_path))
    document_id, created = upsert_document(
        conn,
        title="FAQ 行级批次",
        file_path="original-docs/faq-row-batch.xlsx",
        source_type="excel",
        sha256="unit-test-faq-row-batch",
        meta={"category": "faq"},
    )
    assert created is True
    with conn.cursor() as cur:
        cur.execute("update documents set batch = 'B23' where id = %s", (document_id,))
    conn.commit()
    try:
        parse_document(
            conn, document_id=document_id, settings=settings, embedder=fake_embedder
        )
        with conn.cursor() as cur:
            cur.execute(
                "select evidence_id, batch, meta_json ->> 'batchFromRow' from chunks "
                "where document_id = %s order by evidence_id",
                (document_id,),
            )
            rows = cur.fetchall()
            cur.execute(
                "select meta_json ->> 'rowBatches' from documents where id = %s",
                (document_id,),
            )
            row_batches = int(cur.fetchone()[0])
        assert [row[1] for row in rows] == ["B22", "B23"], "行内有值优先，空行继承文档批次"
        assert rows[0][2] == "true", "行级批次要打 batchFromRow 标记，文档级改批次才不敢覆盖"
        assert rows[1][2] is None
        assert row_batches == 1, "文档 meta 要能看出有几行带了行级批次"
    finally:
        with conn.cursor() as cur:
            cur.execute("delete from documents where id = %s", (document_id,))
        conn.commit()


def test_parse_table_writes_row_chunks(conn, tmp_path, fake_embedder) -> None:
    """数据表格（参数表）入库：一行一条切片，出处是工作表行号，证据前缀 X。"""
    from openpyxl import Workbook

    docs_dir = tmp_path / "original-docs"
    docs_dir.mkdir()
    target = docs_dir / "params.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "参数表"
    sheet.append(["型号", "间隙标准(mm)"])
    sheet.append(["BK", 0.4])
    sheet.append(["DZS", 0.3])
    workbook.save(target)

    settings = Settings(data_dir=str(tmp_path))
    document_id, created = upsert_document(
        conn,
        title="参数表",
        file_path="original-docs/params.xlsx",
        source_type="excel",
        sha256="unit-test-table",
        meta={"category": "table"},
    )
    assert created is True
    try:
        result = parse_document(
            conn, document_id=document_id, settings=settings, embedder=fake_embedder
        )
        assert result.chunks == 2
        with conn.cursor() as cur:
            cur.execute(
                "select evidence_id, meta_json ->> 'loc', content from chunks "
                "where document_id = %s order by evidence_id",
                (document_id,),
            )
            rows = cur.fetchall()
            cur.execute("select meta_json from documents where id = %s", (document_id,))
            doc_meta = cur.fetchone()[0]
        assert rows[0][0].startswith("X"), "数据表格的证据前缀应为 X"
        assert rows[0][1] == "参数表 第2行"
        assert "型号：BK" in rows[0][2] and "间隙标准(mm)：0.4" in rows[0][2]
        assert doc_meta["table"] is True
        assert doc_meta["rows"] == 2
    finally:
        with conn.cursor() as cur:
            cur.execute("delete from documents where id = %s", (document_id,))
        conn.commit()


def test_parse_document_writes_chunks_with_page_provenance(
    conn, ingest_env, fake_embedder
) -> None:
    settings, document_id = ingest_env
    result = parse_document(
        conn, document_id=document_id, settings=settings, embedder=fake_embedder
    )

    assert result.pages == 3
    assert result.used_toc is True
    assert result.chunks == len(fake_embedder.calls[0])
    assert result.embedding_dim == 1024

    with conn.cursor() as cur:
        cur.execute(
            "select evidence_id, content, page_from, page_to, section_no, embedding is not null "
            "from chunks where document_id = %s order by id",
            (document_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == result.chunks
    for evidence_id, content, page_from, page_to, _section_no, has_vector in rows:
        assert evidence_id.startswith("M"), "手册的证据 ID 前缀应为 M"
        assert content.strip()
        assert page_from and page_to and page_from <= page_to
        assert has_vector is True, "S2 必须把向量写进库（否则 S3 要重跑）"

    bearing = next(row for row in rows if "听音杆" in row[1])
    assert (bearing[2], bearing[3]) == (2, 2)
    assert bearing[4] == "6.4.2"

    with conn.cursor() as cur:
        cur.execute("select status, meta_json from documents where id = %s", (document_id,))
        status, meta = cur.fetchone()
    assert status == "parsed"
    assert meta["chunks"] == result.chunks
    assert meta["embeddingDim"] == 1024


def test_reparse_is_idempotent(conn, ingest_env, fake_embedder) -> None:
    settings, document_id = ingest_env
    first = parse_document(
        conn, document_id=document_id, settings=settings, embedder=fake_embedder
    )
    second = parse_document(
        conn, document_id=document_id, settings=settings, embedder=fake_embedder
    )
    assert first.chunks == second.chunks

    with conn.cursor() as cur:
        cur.execute("select count(*) from chunks where document_id = %s", (document_id,))
        assert cur.fetchone()[0] == second.chunks, "重复解析不能翻倍"


def test_missing_file_marks_document_failed(conn, ingest_env, fake_embedder) -> None:
    settings, document_id = ingest_env
    with conn.cursor() as cur:
        cur.execute(
            "update documents set file_path = 'original-docs/not-there.pdf' where id = %s",
            (document_id,),
        )
    conn.commit()

    with pytest.raises(IngestError):
        parse_document(conn, document_id=document_id, settings=settings, embedder=fake_embedder)

    with conn.cursor() as cur:
        cur.execute("select status, meta_json from documents where id = %s", (document_id,))
        status, meta = cur.fetchone()
    assert status == "failed"
    assert "不存在" in meta["error"]


def test_scanned_pdf_is_rejected(conn, ingest_env, fake_embedder, tmp_path) -> None:
    import pymupdf

    settings, document_id = ingest_env
    scanned = tmp_path / "original-docs" / "scanned.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.draw_line((72, 72), (200, 72))
    document.save(scanned)
    document.close()
    with conn.cursor() as cur:
        cur.execute(
            "update documents set file_path = 'original-docs/scanned.pdf' where id = %s",
            (document_id,),
        )
    conn.commit()

    with pytest.raises(IngestError) as excinfo:
        parse_document(conn, document_id=document_id, settings=settings, embedder=fake_embedder)
    assert "OCR" in str(excinfo.value)


def test_evidence_id_format_and_uniqueness() -> None:
    assert evidence_id_for("manual", 1, 1) == "M01001"
    assert evidence_id_for("ticket", 12, 340) == "T12340"
    assert evidence_id_for("unknown", 3, 7) == "E03007"
    sample = {evidence_id_for("manual", doc, seq) for doc in range(1, 20) for seq in range(1, 400)}
    assert len(sample) == 19 * 399


def test_resolve_path_rejects_escape(tmp_path) -> None:
    settings = Settings(data_dir=str(tmp_path))
    with pytest.raises(IngestError):
        resolve_document_path(settings, "../outside.pdf")
    with pytest.raises(IngestError):
        resolve_document_path(settings, "/etc/passwd")
