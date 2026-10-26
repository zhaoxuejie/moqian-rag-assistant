"""OpenAI 兼容客户端：三件套（base_url / model / api_key）直连，不绑厂商 SDK。

只做机制：发请求、解析 SSE、分类错误、按需重试。
行业词汇（系统提示词、回答风格、安全话术）由 `app` 层注入 —— 见架构 §4.1 纪律 6。

针对 D2 实测的两个坑：
1. **推理模型**：`delta.reasoning_content` 必须丢弃，绝不进答案与引用；
   判空时要区分"真没内容"与"被推理吃光"（`LlmEmptyContentError`，并提示加大 max_tokens）。
2. **预算**：`max_tokens` 默认 ≥800（推理 token 也算在内），配置层兜底不允许低于 800。
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from .errors import (
    LlmAuthError,
    LlmConfigError,
    LlmEmptyContentError,
    LlmError,
    LlmModelNotFoundError,
    LlmRateLimitError,
    LlmUpstreamError,
)

# D2 实测：16 会让推理模型 content 为空；800 是安全下限
MIN_MAX_TOKENS = 800
DEFAULT_TEMPERATURE = 0.2


@dataclass(frozen=True)
class LlmConfig:
    """客户端配置。字段名与 `app.config.Settings` 的 llm_* 一一对应。"""

    base_url: str
    model: str
    api_key: str
    timeout_seconds: float = 60.0
    max_tokens: int = 1200
    max_retries: int = 2
    connect_timeout_seconds: float = 10.0

    def validate(self) -> None:
        if not self.base_url:
            raise LlmConfigError(
                "未配置 LLM_BASE_URL", detail="检查 .env 的 BASE_URL / LLM_BASE_URL"
            )
        if not self.api_key:
            raise LlmConfigError("未配置 LLM_API_KEY", detail="检查 .env 的 API_KEY / LLM_API_KEY")
        if not self.model:
            raise LlmConfigError("未配置 LLM_MODEL", detail="检查 .env 的 MODEL / LLM_MODEL")

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/v1/chat/completions"


@dataclass
class LlmResult:
    """非流式调用结果。"""

    text: str
    model: str
    elapsed_ms: int
    usage: dict[str, Any] = field(default_factory=dict)
    reasoning_chars: int = 0
    """被丢弃的 reasoning 字符数：只用于观测推理开销，不进答案。"""


@dataclass
class StreamStats:
    """流式调用结束后可读的统计。"""

    chunks: int = 0
    chars: int = 0
    reasoning_chars: int = 0
    first_token_ms: int | None = None
    elapsed_ms: int = 0
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class LlmClient:
    """OpenAI 兼容 chat/completions 客户端（同步 httpx，便于在 FastAPI 线程池里跑）。

    `transport` 仅用于测试注入（httpx.MockTransport），生产传 None。
    """

    def __init__(self, config: LlmConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self._transport = transport

    # ---------- 对外接口 ----------
    def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> LlmResult:
        """非流式补全。适合评测、任务型调用。"""
        started = time.perf_counter()
        data = self._request(messages, max_tokens=max_tokens, temperature=temperature, stream=False)
        elapsed = int((time.perf_counter() - started) * 1000)
        return self._parse_completion(data, elapsed)

    def stream(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        stats: StreamStats | None = None,
    ) -> Iterator[str]:
        """流式补全：只 yield `content` 增量，reasoning 一律丢弃。

        统计写入传入的 `stats` 对象（便于调用方在流结束后取首字延迟）。
        同步迭代器：FastAPI 的 StreamingResponse 会在线程池里跑同步生成器。
        """
        return self._iter_stream(messages, max_tokens, temperature, stats or StreamStats())

    def stream_with_stats(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        stats: StreamStats | None = None,
    ) -> tuple[Iterator[str], StreamStats]:
        """返回 (迭代器, 统计对象)，方便调用方边迭代边读首字延迟。"""
        stats = stats or StreamStats()
        return self._iter_stream(messages, max_tokens, temperature, stats), stats

    # ---------- 内部实现 ----------
    def _payload(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int | None,
        temperature: float,
        stream: bool,
    ) -> dict[str, Any]:
        budget = max_tokens if max_tokens is not None else self.config.max_tokens
        if budget < MIN_MAX_TOKENS:
            # 不静默接受过小预算：推理模型会返回空 content（D2）
            budget = MIN_MAX_TOKENS
        return {
            "model": self.config.model,
            "messages": list(messages),
            "stream": stream,
            "max_tokens": budget,
            "temperature": temperature,
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _client(self) -> httpx.Client:
        timeout = httpx.Timeout(
            self.config.timeout_seconds, connect=self.config.connect_timeout_seconds
        )
        return httpx.Client(timeout=timeout, transport=self._transport)

    def _request(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int | None,
        temperature: float,
        stream: bool,
    ) -> dict[str, Any]:
        self.config.validate()
        payload = self._payload(
            messages, max_tokens=max_tokens, temperature=temperature, stream=stream
        )
        attempts = self.config.max_retries + 1
        last_error: LlmError | None = None
        for attempt in range(attempts):
            try:
                with self._client() as client:
                    resp = client.post(
                        self.config.chat_completions_url, headers=self._headers(), json=payload
                    )
                self._raise_for_status(resp)
                return resp.json()
            except LlmError as exc:
                last_error = exc
                if not exc.retryable or attempt == attempts - 1:
                    raise
                time.sleep(min(2**attempt, 8) * 0.5)
        raise last_error or LlmUpstreamError("未知错误")

    def _iter_stream(
        self,
        messages: Sequence[dict[str, str]],
        max_tokens: int | None,
        temperature: float,
        stats: StreamStats,
    ) -> Iterator[str]:
        self.config.validate()
        payload = self._payload(
            messages, max_tokens=max_tokens, temperature=temperature, stream=True
        )
        started = time.perf_counter()
        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            emitted = 0
            try:
                with self._client() as client:
                    with client.stream(
                        "POST",
                        self.config.chat_completions_url,
                        headers=self._headers(),
                        json=payload,
                    ) as resp:
                        self._raise_for_status(resp)
                        for raw_line in resp.iter_lines():
                            line = raw_line.strip()
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[len("data:") :].strip()
                            if data == "[DONE]":
                                break
                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            stats.model = event.get("model") or stats.model
                            if event.get("usage"):
                                stats.usage = event["usage"]
                            for choice in event.get("choices") or []:
                                delta = choice.get("delta") or {}
                                reasoning = delta.get("reasoning_content")
                                if reasoning:
                                    stats.reasoning_chars += len(reasoning)
                                content = delta.get("content")
                                if not content:
                                    continue
                                if stats.first_token_ms is None:
                                    stats.first_token_ms = int(
                                        (time.perf_counter() - started) * 1000
                                    )
                                emitted += 1
                                stats.chunks = emitted
                                stats.chars += len(content)
                                yield content
                stats.elapsed_ms = int((time.perf_counter() - started) * 1000)
                if emitted == 0:
                    raise LlmEmptyContentError(
                        "模型返回空内容",
                        detail=(
                            f"reasoning 分片 {stats.reasoning_chars} 字符；"
                            "推理 token 也计入 max_tokens，请加大预算（≥800）"
                            if stats.reasoning_chars
                            else "上游返回 200 但没有 content 分片"
                        ),
                    )
                return
            except LlmError as exc:
                if emitted:
                    # 已经吐了一半，重试会让前端出现重复内容 —— 交给上层兜底（4.8A 失败卡）
                    raise
                if not exc.retryable or attempt == attempts - 1:
                    raise
                time.sleep(min(2**attempt, 8) * 0.5)

    def _parse_completion(self, data: dict[str, Any], elapsed_ms: int) -> LlmResult:
        choices = data.get("choices") or []
        if not choices:
            raise LlmUpstreamError("响应里没有 choices", detail=str(data)[:200])
        message = choices[0].get("message") or {}
        content = (message.get("content") or "").strip()
        reasoning = message.get("reasoning_content") or ""
        if not content:
            raise LlmEmptyContentError(
                "模型返回空内容",
                detail=(
                    f"reasoning 长度 {len(reasoning)}；推理 token 也计入 max_tokens，请加大预算"
                    if reasoning
                    else "上游返回 200 但没有 content"
                ),
            )
        return LlmResult(
            text=content,
            model=data.get("model") or self.config.model,
            elapsed_ms=elapsed_ms,
            usage=data.get("usage") or {},
            reasoning_chars=len(reasoning),
        )

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        status = resp.status_code
        if status < 400:
            return
        body = ""
        try:
            body = resp.read().decode("utf-8", "replace")[:500]
        except Exception:  # noqa: BLE001 - 读错误体失败不能掩盖原始错误
            body = "<无法读取响应体>"
        if status in (401, 403):
            raise LlmAuthError(f"鉴权失败（HTTP {status}）", detail=body)
        if status == 404:
            raise LlmModelNotFoundError("接口或模型不存在（HTTP 404）", detail=body)
        if status == 429:
            raise LlmRateLimitError("限流或额度不足（HTTP 429）", detail=body)
        if status >= 500:
            raise LlmUpstreamError(f"上游错误（HTTP {status}）", detail=body)
        raise LlmError(f"请求被拒绝（HTTP {status}）", detail=body)


def config_from_settings(settings: Any) -> LlmConfig:
    """从 `app.config.Settings` 构造客户端配置。

    用鸭子类型而不是 import：`modules` 不反向依赖 `app`（架构 §4.1 依赖单向）。
    """
    return LlmConfig(
        base_url=getattr(settings, "llm_base_url", ""),
        model=getattr(settings, "llm_model", ""),
        api_key=getattr(settings, "llm_api_key", ""),
        timeout_seconds=float(getattr(settings, "llm_timeout_seconds", 60.0)),
        max_tokens=int(getattr(settings, "llm_max_tokens", 1200)),
        max_retries=int(getattr(settings, "llm_max_retries", 2)),
    )
