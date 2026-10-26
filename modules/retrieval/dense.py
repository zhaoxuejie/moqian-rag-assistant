"""Dense 检索：pgvector 余弦相似度，直接走 HNSW 索引。

要点：
- **距离算子用 `<=>`**（余弦距离），`score = 1 - distance`，分数越高越相关；
- 只在 `disabled = false` 且 `embedding is not null` 的切片里搜（软删除不进检索池）；
- 结构化条件（型号/批次）作为**可选过滤**传入；过滤后命中太少时由管线决定是否回退全库
  （纪律 2：未知字段留空走全库检索，绝不默认收窄）。

过滤语义（2026-10 修正）：
- 批次为空 = **不限批次**（上传提示「不明确可留空」），所以过滤是 `batch = 值 or batch is null`；
- 型号为空 = **全型号适用**，同理包含 NULL；
- 型号支持调用方传入的等价写法集合（如 BK / BK系列），只做等值集合匹配，不做模糊匹配。
"""

from __future__ import annotations

from collections.abc import Sequence

import psycopg

from .types import ScoredChunk


def vector_literal(vector: list[float]) -> str:
    """pgvector 文本格式 `[0.1,0.2]`；定长小数避免二进制协议适配问题。"""
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


_COLUMNS = """
    c.id, c.evidence_id, c.content, c.section_no, c.page_from, c.page_to,
    c.batch, c.manual_version, d.device_model,
    coalesce(c.meta_json ->> 'loc', null) as loc,
    1 - (c.embedding <=> %s::vector) as score
"""


def dense_search(
    conn: psycopg.Connection,
    vector: list[float],
    *,
    top_k: int,
    model: str | None = None,
    batch: str | None = None,
    model_aliases: Sequence[str] | None = None,
) -> list[ScoredChunk]:
    """向量召回 top_k。过滤条件用参数绑定，不拼 SQL 字符串。"""
    literal = vector_literal(vector)
    where = [
        "c.disabled = false",
        "c.embedding is not null",
        "d.lifecycle not in ('disabled', 'archived')",
    ]
    params: list[object] = [literal]
    if model:
        candidates = list(dict.fromkeys([model, *(model_aliases or ())]))
        where.append("(d.device_model is null or d.device_model = any(%s))")
        params.append(candidates)
    if batch:
        where.append("(c.batch is null or c.batch = %s)")
        params.append(batch)
    params.extend([literal, top_k])

    sql = (
        "select " + _COLUMNS + " from chunks c join documents d on d.id = c.document_id "
        "where " + " and ".join(where) + " order by c.embedding <=> %s::vector limit %s"
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        ScoredChunk(
            chunk_id=int(row[0]),
            evidence_id=row[1],
            content=row[2],
            section_no=row[3],
            page_from=row[4],
            page_to=row[5],
            batch=row[6],
            version=row[7],
            model=row[8],
            loc=row[9],
            dense_score=float(row[10]),
            sources=["dense"],
        )
        for row in rows
    ]
