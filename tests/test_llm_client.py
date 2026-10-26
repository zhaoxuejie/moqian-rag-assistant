"""LLM 客户端单测：不联网，用 httpx.MockTransport 造响应。

覆盖的都是 D2 实测踩过的坑：
- 推理模型返回 `reasoning_content`，必须丢弃且不影响 content；
- `max_tokens` 过小导致 content 为空，要报 `LlmEmptyContentError` 而不是当成"没有回答"；
- 限流/5xx 可重试，鉴权/404 不重试。
"""

from __future__ import annotations

import json

import httpx
import pytest

from modules.llm import (
    MIN_MAX_TOKENS,
    LlmAuthError,
    LlmClient,
    LlmConfig,
    LlmEmptyContentError,
    LlmRateLimitError,
    LlmUpstreamError,
)


def make_config(**overrides) -> LlmConfig:
    base = {
        "base_url": "https://llm.example",
        "model": "deepseek-flash",
        "api_key": "sk-test",
        "max_retries": 0,
        "timeout_seconds": 5,
    }
    base.update(overrides)
    return LlmConfig(**base)


def make_client(handler, config: LlmConfig | None = None) -> LlmClient:
    return LlmClient(config or make_config(), transport=httpx.MockTransport(handler))


def sse_body(deltas: list[dict]) -> bytes:
    lines = []
    for delta in deltas:
        chunk = {"model": "deepseek-flash", "choices": [{"delta": delta}]}
        lines.append(f"data: {json.dumps(chunk)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


def test_complete_discards_reasoning() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["messages"][0]["content"] == "你好"
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [
                    {
                        "message": {
                            "content": "链路正常",
                            "reasoning_content": "用户只是打招呼，简短回复即可",
                        }
                    }
                ],
                "usage": {"completion_tokens_details": {"reasoning_tokens": 12}},
            },
        )

    result = make_client(handler).complete([{"role": "user", "content": "你好"}])
    assert result.text == "链路正常"
    assert result.reasoning_chars == len("用户只是打招呼，简短回复即可")
    assert result.usage["completion_tokens_details"]["reasoning_tokens"] == 12


def test_empty_content_with_reasoning_is_reported() -> None:
    """D2：max_tokens 太小 → 有 reasoning、没 content。必须显式报错并提示加大预算。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "", "reasoning_content": "想了很久但被截断"}}
                ]
            },
        )

    with pytest.raises(LlmEmptyContentError) as excinfo:
        make_client(handler).complete([{"role": "user", "content": "问"}])
    assert "max_tokens" in (excinfo.value.detail or "")


def test_max_tokens_has_floor() -> None:
    """过小的预算会被抬到 800，而不是静默接受。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}], "model": "m"}
        )

    make_client(handler).complete([{"role": "user", "content": "x"}], max_tokens=16)
    assert seen["max_tokens"] == MIN_MAX_TOKENS


def test_stream_yields_only_content() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_body(
                [
                    {"reasoning_content": "先想想…"},
                    {"content": "结"},
                    {"reasoning_content": "再想想…"},
                    {"content": "论"},
                ]
            ),
            headers={"content-type": "text/event-stream"},
        )

    stats = None
    iterator, stats = make_client(handler).stream_with_stats(
        [{"role": "user", "content": "问"}]
    )
    pieces = list(iterator)
    assert pieces == ["结", "论"]
    assert stats.chars == 2
    assert stats.chunks == 2
    assert stats.reasoning_chars == len("先想想…") + len("再想想…")
    assert stats.first_token_ms is not None


def test_stream_without_any_content_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse_body([{"reasoning_content": "全是推理"}]),
            headers={"content-type": "text/event-stream"},
        )

    iterator, _stats = make_client(handler).stream_with_stats(
        [{"role": "user", "content": "问"}]
    )
    with pytest.raises(LlmEmptyContentError):
        list(iterator)


@pytest.mark.parametrize(
    ("status", "error_type", "retryable"),
    [
        (401, LlmAuthError, False),
        (429, LlmRateLimitError, True),
        (500, LlmUpstreamError, True),
    ],
)
def test_error_classification(status: int, error_type: type[Exception], retryable: bool) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="boom")

    with pytest.raises(error_type) as excinfo:
        make_client(handler).complete([{"role": "user", "content": "x"}])
    assert excinfo.value.retryable is retryable


def test_retry_then_success() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "第二次成功"}}], "model": "m"}
        )

    client = make_client(handler, make_config(max_retries=1))
    assert client.complete([{"role": "user", "content": "x"}]).text == "第二次成功"
    assert calls["n"] == 2


def test_missing_api_key_is_config_error() -> None:
    from modules.llm import LlmConfigError

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("配置不全都该在发请求前拦住")

    client = LlmClient(make_config(api_key=""), transport=httpx.MockTransport(handler))
    with pytest.raises(LlmConfigError):
        client.complete([{"role": "user", "content": "x"}])
