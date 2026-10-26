"""Cross-encoder 重排客户端（腾讯云 AI Search `/v1/rerank` 已验证）。

机制放在 modules：请求/响应解析、超时、错误分类；
provider 选择与模型名由 app 层配置注入，不在模块里写死。

实测（2026-10-19）：
- `POST https://bj.aisearch.tencentelasticsearch.com/v1/rerank`
- `{model, query, documents:[...], top_n, return_documents}`
- 返回 `{results:[{index, relevance_score, document?}], usage:{total_tokens}}`；
- 结果按 score 降序，但**不保证包含所有文档**，因此按 index 回填，
  缺失项给最低分，保持与输入同序。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import (
    LlmAuthError,
    LlmConfigError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmUpstreamError,
)


@dataclass(frozen=True)
class RerankerConfig:
    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: float = 10.0

    def validate(self) -> None:
        if not self.base_url:
            raise LlmConfigError("未配置 RERANKER_BASE_URL", detail="示例：https://.../v1")
        if not self.model:
            raise LlmConfigError("未配置 RERANKER_MODEL", detail="示例：bge-reranker-v2-m3")

    @property
    def rerank_url(self) -> str:
        return self.base_url.rstrip("/") + "/rerank"


@dataclass
class RerankResult:
    scores: list[float]
    model: str
    elapsed_ms: int
    total_tokens: int = 0


class RerankerClient:
    """同步 rerank 客户端；调用方负责在失败时回退 RRF（不阻断回答）。"""

    def __init__(
        self, config: RerankerConfig, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.config = config
        self._transport = transport

    def rerank(self, query: str, documents: list[str]) -> RerankResult:
        if not documents:
            return RerankResult(scores=[], model=self.config.model, elapsed_ms=0)
        self.config.validate()
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": self.config.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
            "return_documents": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        try:
            with httpx.Client(
                timeout=self.config.timeout_seconds,
                transport=self._transport,
                trust_env=False,
            ) as client:
                resp = client.post(self.config.rerank_url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise LlmTimeoutError("rerank 请求超时") from exc
        except httpx.HTTPError as exc:
            raise LlmUpstreamError(f"rerank 网络错误：{exc}") from exc

        if resp.status_code == 401:
            raise LlmAuthError("rerank 鉴权失败（检查 RERANKER_API_KEY）")
        if resp.status_code == 429:
            raise LlmRateLimitError("rerank 触发限流")
        if resp.status_code >= 400:
            raise LlmUpstreamError(
                f"rerank 上游错误（HTTP {resp.status_code}）：{resp.text[:200]}"
            )
        try:
            body = resp.json()
            raw_results = body.get("results") or []
        except (ValueError, AttributeError) as exc:
            raise LlmUpstreamError("rerank 返回不是合法 JSON") from exc

        # 默认 -1e9：API 只返回 top_n 条时，未返回的文档排到最后，不冒充 0 分
        scores = [-1e9] * len(documents)
        for item in raw_results:
            try:
                index = int(item["index"])
                score = float(item["relevance_score"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= index < len(scores):
                scores[index] = score
        usage = body.get("usage") or {}
        return RerankResult(
            scores=scores,
            model=str(body.get("model") or self.config.model),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            total_tokens=int(usage.get("total_tokens") or 0),
        )
