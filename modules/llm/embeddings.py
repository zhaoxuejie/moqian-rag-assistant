"""OpenAI 兼容 embeddings 客户端（S2 起用于切片向量化）。

为什么单独一个客户端：embedding 与 chat 的失败模式不同（批量大小、维度校验、
本地推理超时），混在一起会让 `modules/llm` 越来越难测。

实测（Ollama + `bge-m3:latest`，2026-10-17）：
- `POST http://localhost:11434/v1/embeddings`，请求体 `{model, input: [...]}`；
- 返回 `{data:[{embedding:[...]}], usage:{prompt_tokens}}`，
  维度 **1024**（与 `chunks.embedding vector(1024)` 一致）；
- 单条冷启动约 9.2s（模型加载），批量 8 条约 754ms；
- 容器内用 `host.docker.internal:11434` 访问宿主机 Ollama。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import (
    LlmConfigError,
    LlmRateLimitError,
    LlmUpstreamError,
)


class EmbeddingDimensionError(LlmConfigError):
    """返回维度与库表 `vector(N)` 不一致 —— 属于配置错误，重试没有意义。"""

    code = "EMBEDDING_DIM_MISMATCH"


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str
    model: str
    dim: int = 1024
    api_key: str = ""
    timeout_seconds: float = 120.0
    batch_size: int = 16
    max_retries: int = 2

    def validate(self) -> None:
        if not self.base_url:
            raise LlmConfigError(
                "未配置 EMBEDDING_BASE_URL", detail="示例：http://localhost:11434/v1"
            )
        if not self.model:
            raise LlmConfigError("未配置 EMBEDDING_MODEL", detail="示例：bge-m3:latest")

    @property
    def embeddings_url(self) -> str:
        return self.base_url.rstrip("/") + "/embeddings"


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dim: int
    elapsed_ms: int
    prompt_tokens: int = 0


class EmbeddingClient:
    """按批调用 embeddings 接口，返回与输入同序的向量。"""

    def __init__(
        self, config: EmbeddingConfig, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.config = config
        self._transport = transport

    def embed(self, texts: list[str]) -> list[list[float]]:
        """全量向量化（自动分批）。空输入直接返回空列表，不发请求。"""
        started = time.perf_counter()
        vectors: list[list[float]] = []
        prompt_tokens = 0
        for start in range(0, len(texts), self.config.batch_size):
            batch = texts[start : start + self.config.batch_size]
            if not batch:
                continue
            result = self._embed_batch(batch)
            vectors.extend(result["vectors"])
            prompt_tokens += result["prompt_tokens"]
        self.last = EmbeddingResult(
            vectors=vectors,
            model=self.config.model,
            dim=self.config.dim,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=prompt_tokens,
        )
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    # ---------- 内部 ----------
    def _embed_batch(self, batch: list[str]) -> dict[str, Any]:
        self.config.validate()
        payload = {"model": self.config.model, "input": batch}
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        attempts = self.config.max_retries + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(self.config.timeout_seconds),
                    transport=self._transport,
                ) as client:
                    resp = client.post(self.config.embeddings_url, json=payload, headers=headers)
                if resp.status_code >= 400:
                    if resp.status_code == 429:
                        raise LlmRateLimitError(
                            "embeddings 限流（HTTP 429）", detail=resp.text[:300]
                        )
                    if resp.status_code >= 500:
                        raise LlmUpstreamError(
                            f"embeddings 上游错误（HTTP {resp.status_code}）",
                            detail=resp.text[:300],
                        )
                    raise LlmConfigError(
                        f"embeddings 请求被拒绝（HTTP {resp.status_code}）", detail=resp.text[:300]
                    )
                data = resp.json()
                return self._parse(data, expected=len(batch))
            except (LlmRateLimitError, LlmUpstreamError) as exc:
                last_error = exc
                if attempt == attempts - 1:
                    raise
                time.sleep(min(2**attempt, 8) * 0.5)
            except httpx.HTTPError as exc:  # 连接失败/超时
                last_error = LlmUpstreamError(f"embeddings 连接失败：{exc}")
                if attempt == attempts - 1:
                    raise last_error from exc
                time.sleep(min(2**attempt, 8) * 0.5)
        raise last_error or LlmUpstreamError("embeddings 未知错误")

    def _parse(self, data: dict[str, Any], *, expected: int) -> dict[str, Any]:
        rows = data.get("data") or []
        if len(rows) != expected:
            raise LlmUpstreamError(
                f"embeddings 返回条数不符：期望 {expected}，实际 {len(rows)}",
                detail=str(data)[:300],
            )
        vectors: list[list[float]] = []
        for row in rows:
            vector = row.get("embedding") or []
            if len(vector) != self.config.dim:
                raise EmbeddingDimensionError(
                    f"embedding 维度不符：库表 vector({self.config.dim})，模型返回 {len(vector)}",
                    detail="换模型时需同步改 EMBEDDING_DIM 并重建 chunk 表/列",
                )
            vectors.append([float(value) for value in vector])
        return {
            "vectors": vectors,
            "prompt_tokens": int((data.get("usage") or {}).get("prompt_tokens", 0)),
        }

    def health(self) -> tuple[bool, str]:
        """自检：发一条最小请求，返回 (是否可用, 说明)。供后台配置页「测试模型」用。"""
        try:
            vector = self.embed_one("健康检查")
        except Exception as exc:  # noqa: BLE001 - 自检只关心能不能用
            return False, str(exc)
        return True, f"OK dim={len(vector)} model={self.config.model}"
