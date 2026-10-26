"""Reranker 客户端（腾讯云 /v1/rerank 响应形状）离线测试，不联网。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from modules.llm import LlmAuthError
from modules.llm.reranker import RerankerClient, RerankerConfig
from modules.retrieval import RetrievalPipeline, RetrievalSettings, ScoredChunk


def _client(handler, *, key: str = "test-key") -> RerankerClient:
    return RerankerClient(
        RerankerConfig(
            base_url="https://rerank.invalid/v1",
            model="bge-reranker-v2-m3",
            api_key=key,
            timeout_seconds=3,
        ),
        transport=httpx.MockTransport(handler),
    )


def test_scores_are_returned_in_document_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/rerank"
        assert request.headers["authorization"] == "Bearer test-key"
        body = request.read().decode()
        assert '"model":"bge-reranker-v2-m3"' in body
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.1},
                ],
                "model": "bge-reranker-v2-m3",
                "usage": {"total_tokens": 12},
            },
        )

    result = _client(handler).rerank("q", ["a", "b"])
    assert result.scores == [0.1, 0.9]
    assert result.model == "bge-reranker-v2-m3"
    assert result.total_tokens == 12


def test_missing_document_gets_lowest_score_not_zero() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 0.5}]},
        )

    result = _client(handler).rerank("q", ["a", "b", "c"])
    assert result.scores[0] == 0.5
    assert result.scores[1] == pytest.approx(-1e9)
    assert result.scores[2] == pytest.approx(-1e9)


def test_auth_error_is_typed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "missing_api_key"}})

    with pytest.raises(LlmAuthError):
        _client(handler).rerank("q", ["a"])


def test_empty_documents_make_no_request() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    result = _client(handler).rerank("q", [])
    assert result.scores == []
    assert called is False


class _FakeEmbedder:
    def embed_one(self, _text: str) -> list[float]:
        return [0.0] * 1024


class _FakeBm25Index:
    def search(self, _tokens, top_k: int):
        return []


class _FakeCache:
    def get(self, _conn):
        return _FakeBm25Index()


class _FakeReranker:
    def __init__(self, scores: list[float] | None = None, fail: bool = False) -> None:
        self.scores = scores
        self.fail = fail

    def rerank(self, _query: str, documents: list[str]):
        if self.fail:
            raise RuntimeError("reranker down")
        return SimpleNamespace(scores=self.scores or list(range(len(documents))))


def _chunks(count: int) -> list[ScoredChunk]:
    return [
        ScoredChunk(
            chunk_id=i,
            evidence_id=f"M{i}",
            content=f"内容 {i}",
            rrf_score=1.0 / (i + 1),
            sources=["dense"],
        )
        for i in range(count)
    ]


def test_pipeline_applies_reranker_order(monkeypatch) -> None:
    from modules.retrieval import pipeline as pipeline_module

    chunks = _chunks(3)
    monkeypatch.setattr(
        pipeline_module,
        "dense_search",
        lambda *_args, **_kwargs: list(chunks),
    )
    fake = _FakeReranker(scores=[0.0, 2.0, 1.0])
    pipe = RetrievalPipeline(
        settings=RetrievalSettings(final_top_n=3, rerank_candidates=3),
        embedder=_FakeEmbedder(),
        bm25_cache=_FakeCache(),
        reranker=fake,
    )
    result = pipe.retrieve(
        None,  # dense/bm25 都被替换，不需要真实连接
        question="测试",
    )
    assert result.reranked is True
    assert [item.evidence_id for item in result.items] == ["M1", "M2", "M0"]
    assert result.items[0].rerank_score == 2.0


def test_pipeline_falls_back_to_rrf_when_reranker_fails(monkeypatch) -> None:
    from modules.retrieval import pipeline as pipeline_module

    chunks = _chunks(3)
    monkeypatch.setattr(
        pipeline_module,
        "dense_search",
        lambda *_args, **_kwargs: list(chunks),
    )
    pipe = RetrievalPipeline(
        settings=RetrievalSettings(final_top_n=3, rerank_candidates=3),
        embedder=_FakeEmbedder(),
        bm25_cache=_FakeCache(),
        reranker=_FakeReranker(fail=True),
    )
    result = pipe.retrieve(None, question="测试")
    assert result.reranked is False
    assert [item.rerank_score for item in result.items] == [None, None, None]
