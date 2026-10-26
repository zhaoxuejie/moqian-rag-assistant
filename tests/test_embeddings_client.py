"""embedding 客户端单测（假 transport，不联网）。

覆盖 D14 的实际风险：
- 维度必须与库表 `vector(1024)` 一致，不一致要报配置错误而不是硬写库；
- 批量按配置切分，次数与顺序正确；
- 429/5xx 可重试、4xx 配置错误不重试。
"""

from __future__ import annotations

import json

import httpx
import pytest

from modules.llm import (
    EmbeddingClient,
    EmbeddingConfig,
    EmbeddingDimensionError,
    LlmConfigError,
    LlmRateLimitError,
)


def make_config(**overrides) -> EmbeddingConfig:
    base = {
        "base_url": "http://embed.example/v1",
        "model": "bge-m3:latest",
        "dim": 4,
        "batch_size": 2,
        "max_retries": 0,
        "timeout_seconds": 5,
    }
    base.update(overrides)
    return EmbeddingConfig(**base)


def make_client(handler, config: EmbeddingConfig | None = None) -> EmbeddingClient:
    return EmbeddingClient(config or make_config(), transport=httpx.MockTransport(handler))


def embed_response(count: int, dim: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "bge-m3",
            "data": [{"embedding": [0.1] * dim} for _ in range(count)],
            "usage": {"prompt_tokens": count},
        },
    )


def test_embed_splits_batches_and_keeps_order() -> None:
    requests: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body["input"])
        return embed_response(len(body["input"]), 4)

    vectors = make_client(handler).embed(["a", "b", "c", "d", "e"])
    assert [len(batch) for batch in requests] == [2, 2, 1], "应按 batch_size=2 分批"
    assert [batch[0] for batch in requests] == ["a", "c", "e"]
    assert len(vectors) == 5


def test_empty_input_makes_no_request() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("空输入不该发请求")

    assert make_client(handler).embed([]) == []


def test_dimension_mismatch_is_config_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return embed_response(1, 1024)  # 库表配的是 4 维，模型却给 1024

    with pytest.raises(EmbeddingDimensionError) as excinfo:
        make_client(handler).embed(["x"])
    assert "1024" in str(excinfo.value) and "4" in str(excinfo.value)


def test_count_mismatch_raises_upstream_error() -> None:
    from modules.llm import LlmUpstreamError

    def handler(_request: httpx.Request) -> httpx.Response:
        return embed_response(1, 4)  # 请求了 2 条，只回 1 条

    with pytest.raises(LlmUpstreamError):
        make_client(handler).embed(["x", "y"])


def test_429_is_retried() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="busy")
        return embed_response(1, 4)

    client = make_client(handler, make_config(max_retries=1))
    assert client.embed_one("x") == [0.1] * 4
    assert calls["n"] == 2


def test_400_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad model")

    with pytest.raises(LlmConfigError):
        make_client(handler, make_config(max_retries=2)).embed(["x"])
    assert calls["n"] == 1


def test_missing_base_url_is_config_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("配置不全都该在发请求前拦住")

    with pytest.raises(LlmConfigError):
        make_client(handler, make_config(base_url="")).embed(["x"])


def test_rate_limit_error_is_retryable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="busy")

    with pytest.raises(LlmRateLimitError) as excinfo:
        make_client(handler).embed(["x"])
    assert excinfo.value.retryable is True
