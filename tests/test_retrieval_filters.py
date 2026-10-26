"""检索过滤 SQL 语义（需要数据库）：批次/型号为空按「不限」处理，型号支持等价写法。

对应真实踩坑：库里型号写 `BK`、问题里写 `BK系列`；批次一边为空。
旧 SQL 用精确等值（`d.device_model = %s`、`c.batch = %s`）直接 0 命中。
"""

from __future__ import annotations

import pytest
from conftest import requires_db

from app.domain import model_match_aliases
from modules.retrieval.dense import dense_search

pytestmark = requires_db

_DIM = 1024


def _unit_vector() -> list[float]:
    vector = [0.0] * _DIM
    vector[0] = 1.0
    return vector


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


@pytest.fixture()
def filtered_corpus(conn):
    """三条切片：A(BK, B22) / B(BK系列, 批次空) / C(DB350, B22)。"""
    docs = [
        ("T-A", "BK", "B22", "BK 手册：制动器间隙按 B22 标准"),
        ("T-B", "BK系列", None, "BK系列手册：制动器间隙通用值"),
        ("T-C", "DB350", "B22", "DB350 手册：专用间隙说明"),
    ]
    doc_ids: list[int] = []
    literal = _literal(_unit_vector())
    with conn.cursor() as cur:
        for evidence_id, model, batch, content in docs:
            cur.execute(
                "insert into documents "
                "(source_type, title, file_path, status, device_model, batch, manual_version) "
                "values ('pdf', %s, %s, 'parsed', %s, %s, 'v1') returning id",
                (f"过滤测试 {evidence_id}", f"original-docs/{evidence_id}.pdf", model, batch),
            )
            doc_id = int(cur.fetchone()[0])
            cur.execute(
                "insert into chunks "
                "(document_id, evidence_id, content, page_from, page_to, batch, manual_version, "
                " meta_json, embedding, bm25_tokens) "
                "values (%s, %s, %s, 1, 1, %s, 'v1', %s::jsonb, %s::vector, %s)",
                (doc_id, evidence_id, content, batch, '{"loc": "§1"}', literal, content),
            )
            doc_ids.append(doc_id)
    conn.commit()
    yield doc_ids
    with conn.cursor() as cur:
        cur.execute("delete from documents where id = any(%s)", (doc_ids,))
    conn.commit()


def test_dense_filter_keeps_null_batch_and_model_alias(conn, filtered_corpus) -> None:
    hits = dense_search(
        conn,
        _unit_vector(),
        top_k=10,
        model="BK系列",
        batch="B22",
        model_aliases=model_match_aliases("BK系列"),
    )
    got = {hit.evidence_id for hit in hits}
    assert {"T-A", "T-B"} <= got, "型号 BK 与 BK系列 应互相匹配；批次为空应视为不限"
    assert "T-C" not in got, "其它型号不能被带进来"
