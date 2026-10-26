"""retrieval：Dense(pgvector) + BM25(jieba) + RRF 融合。S3 落地。

- `dense`：向量召回（pgvector HNSW，余弦）；
- `bm25`：关键词召回（应用层内存索引，token 由入库时的 jieba 预分词提供）；
- `fusion`：RRF 融合（分数不可比，只比名次）；
- `pipeline`：串起来 + 过滤回退策略。

重排（cross-encoder）暂为可插拔开关，默认关闭（D15）。
"""

from .bm25 import Bm25Index, Bm25IndexCache, tokenize_query
from .dense import dense_search, vector_literal
from .fusion import DEFAULT_RRF_K, rrf_fuse
from .pipeline import Embedder, RetrievalPipeline, RetrievalSettings
from .types import RetrievalResult, ScoredChunk

__all__ = [
    "DEFAULT_RRF_K",
    "Bm25Index",
    "Bm25IndexCache",
    "Embedder",
    "RetrievalPipeline",
    "RetrievalResult",
    "RetrievalSettings",
    "ScoredChunk",
    "dense_search",
    "rrf_fuse",
    "tokenize_query",
    "vector_literal",
]
