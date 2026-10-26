"""llm：OpenAI 兼容客户端（chat / streaming / embeddings / rerank），三件套直连，不绑厂商 SDK。

- 机制留在这里：请求、SSE 解析、错误分类、重试、丢弃 reasoning、embedding 分批与维度校验、
  cross-encoder rerank 请求/评分回填；
- 行业词汇留在 `app`：系统提示词、回答风格、安全话术、max_tokens 策略。
"""

from .client import (
    MIN_MAX_TOKENS,
    LlmClient,
    LlmConfig,
    LlmResult,
    StreamStats,
    config_from_settings,
)
from .embeddings import (
    EmbeddingClient,
    EmbeddingConfig,
    EmbeddingDimensionError,
    EmbeddingResult,
)
from .errors import (
    LlmAuthError,
    LlmConfigError,
    LlmEmptyContentError,
    LlmError,
    LlmModelNotFoundError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmUpstreamError,
)
from .reranker import RerankerClient, RerankerConfig, RerankResult

__all__ = [
    "MIN_MAX_TOKENS",
    "EmbeddingClient",
    "EmbeddingConfig",
    "EmbeddingDimensionError",
    "EmbeddingResult",
    "LlmAuthError",
    "LlmClient",
    "LlmConfig",
    "LlmConfigError",
    "LlmEmptyContentError",
    "LlmError",
    "LlmModelNotFoundError",
    "LlmRateLimitError",
    "LlmResult",
    "LlmTimeoutError",
    "LlmUpstreamError",
    "RerankResult",
    "RerankerClient",
    "RerankerConfig",
    "StreamStats",
    "config_from_settings",
]
