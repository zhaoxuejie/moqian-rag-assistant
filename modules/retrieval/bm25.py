"""BM25 检索：应用层内存索引（jieba 预分词已随切片落库）。

为什么不用 PG 的全文检索：中文需要分词，PG 自带没有中文词表；而切片入库时
已经把 `bm25_tokens`（jieba 搜索结果）存下来了，应用层算 BM25 最省事，
P0 语料量（几百到几万切片）完全够用，也符合架构"不引重量级组件"的约束。

实现是标准 BM25（k1=1.5, b=0.75）：
    score(q,d) = Σ_t IDF(t) * tf(t,d)*(k1+1) / (tf + k1*(1-b+b*|d|/avgdl))
"""

from __future__ import annotations

import math
import threading
from collections import Counter
from dataclasses import dataclass

import psycopg

from .types import ScoredChunk

K1 = 1.5
B = 0.75


@dataclass
class _Doc:
    chunk: ScoredChunk
    tokens: Counter[str]
    length: int


class Bm25Index:
    """一次性构建，查询只读；线程安全靠构建后不可变。"""

    def __init__(self, docs: list[tuple[ScoredChunk, list[str]]]) -> None:
        self._docs: list[_Doc] = []
        df: Counter[str] = Counter()
        for chunk, tokens in docs:
            counter = Counter(token for token in tokens if token.strip())
            self._docs.append(_Doc(chunk=chunk, tokens=counter, length=sum(counter.values())))
            df.update(counter.keys())
        self.size = len(self._docs)
        self.avgdl = (
            sum(doc.length for doc in self._docs) / self.size if self.size else 0.0
        )
        self._idf = {
            token: math.log(1 + (self.size - freq + 0.5) / (freq + 0.5))
            for token, freq in df.items()
        }

    def search(self, query_tokens: list[str], *, top_k: int) -> list[ScoredChunk]:
        tokens = [token for token in query_tokens if token in self._idf]
        if not tokens or not self._docs:
            return []
        scored: list[tuple[float, ScoredChunk]] = []
        for doc in self._docs:
            score = 0.0
            for token in tokens:
                tf = doc.tokens.get(token)
                if not tf:
                    continue
                denominator = tf + K1 * (1 - B + B * doc.length / (self.avgdl or 1))
                score += self._idf[token] * tf * (K1 + 1) / denominator
            if score > 0:
                scored.append((score, doc.chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        results: list[ScoredChunk] = []
        for score, chunk in scored[:top_k]:
            results.append(
                ScoredChunk(
                    chunk_id=chunk.chunk_id,
                    evidence_id=chunk.evidence_id,
                    content=chunk.content,
                    section_no=chunk.section_no,
                    page_from=chunk.page_from,
                    page_to=chunk.page_to,
                    batch=chunk.batch,
                    version=chunk.version,
                    model=chunk.model,
                    bm25_score=score,
                    sources=["bm25"],
                )
            )
        return results


class Bm25IndexCache:
    """按（切片总数, 最大 id）判断语料是否变化，变了才重建；避免每问一次全表扫描。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._signature: tuple[int, int, str] | None = None
        self._index: Bm25Index | None = None

    def get(self, conn: psycopg.Connection) -> Bm25Index:
        with self._lock:
            signature = self._corpus_signature(conn)
            if self._index is not None and signature == self._signature:
                return self._index
            docs = self._load_corpus(conn)
            self._index = Bm25Index(docs)
            self._signature = signature
            return self._index

    def invalidate(self) -> None:
        with self._lock:
            self._index = None
            self._signature = None

    @staticmethod
    def _corpus_signature(conn: psycopg.Connection) -> tuple[int, int, str]:
        """语料签名：切片数 + 最大 id + 文档最后更新时间。

        仅用 (count, max id) 无法感知"知识源启用/停用"这类不增删切片的变更，
        因此把 documents.updated_at 也纳入签名，生命周期一改就重建索引。
        """
        with conn.cursor() as cur:
            cur.execute(
                """
                select count(*), coalesce(max(c.id), 0), coalesce(max(d.updated_at)::text, '')
                  from chunks c join documents d on d.id = c.document_id
                 where c.disabled = false and d.lifecycle not in ('disabled', 'archived')
                """
            )
            row = cur.fetchone()
        return int(row[0]), int(row[1]), str(row[2])

    @staticmethod
    def _load_corpus(conn: psycopg.Connection) -> list[tuple[ScoredChunk, list[str]]]:
        with conn.cursor() as cur:
            cur.execute(
                """
                select c.id, c.evidence_id, c.content, c.section_no, c.page_from, c.page_to,
                       c.batch, c.manual_version, d.device_model, c.bm25_tokens,
                       coalesce(c.meta_json ->> 'loc', null) as loc
                  from chunks c join documents d on d.id = c.document_id
                 where c.disabled = false and c.bm25_tokens is not null
                   and d.lifecycle not in ('disabled', 'archived')
                """
            )
            rows = cur.fetchall()
        docs: list[tuple[ScoredChunk, list[str]]] = []
        for row in rows:
            chunk = ScoredChunk(
                chunk_id=int(row[0]),
                evidence_id=row[1],
                content=row[2],
                section_no=row[3],
                page_from=row[4],
                page_to=row[5],
                batch=row[6],
                version=row[7],
                model=row[8],
                loc=row[10],
            )
            docs.append((chunk, (row[9] or "").split()))
        return docs


# 查询侧的虚词/高频疑问词：参与 BM25 只会把重复模板切片顶上来（实测"钢丝绳检查"
# 被 8 份手册的"§3.1 维护周期"挤掉），过滤后 BM25 才聚焦到真正的实词。
_QUERY_STOPWORDS = frozenset(
    {
        "的",
        "了",
        "吗",
        "呢",
        "是",
        "在",
        "和",
        "与",
        "或",
        "及",
        "等",
        "需要",
        "哪些",
        "哪种",
        "什么",
        "怎么",
        "如何",
        "是否",
        "可以",
        "请问",
        "多少",
        "为什么",
    }
)


def tokenize_query(text: str) -> list[str]:
    """查询分词：搜索引擎模式 + 去纯标点/空白/虚词。行业词表由 app 注入（S3 后才需要）。"""
    import jieba

    tokens = [token.strip() for token in jieba.cut_for_search(text)]
    return [
        token
        for token in tokens
        if token and token not in _QUERY_STOPWORDS and any(char.isalnum() for char in token)
    ]
