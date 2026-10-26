"""LLM 错误分类。

分类的目的不是"抛得好看"，而是让上层能决定**要不要重试**、**给用户看什么**：
- 可重试：限流、超时、上游 5xx、连接中断 —— `/chat` 用 `error` 事件带 `retryable: true`；
- 不可重试：鉴权、模型不存在、请求体非法、返回空内容 —— 必须显式暴露给运维，不能静默重试。
"""

from __future__ import annotations


class LlmError(Exception):
    """LLM 调用失败的基类。"""

    retryable: bool = False
    code: str = "LLM_ERROR"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def as_event(self) -> dict:
        """转成 SSE `error` 事件的载荷（契约 SseError.error）。"""
        payload = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.detail:
            payload["detail"] = self.detail
        return payload


class LlmConfigError(LlmError):
    """配置缺失（没填 API key / base_url）—— 属于部署问题，不该在运行时反复重试。"""

    code = "LLM_CONFIG"


class LlmAuthError(LlmError):
    """401/403：key 失效或权限不足。"""

    code = "LLM_AUTH"


class LlmModelNotFoundError(LlmError):
    """404：模型名不存在（配置写错）。"""

    code = "LLM_MODEL_NOT_FOUND"


class LlmRateLimitError(LlmError):
    """429：限流或额度耗尽。"""

    retryable = True
    code = "LLM_RATE_LIMIT"


class LlmTimeoutError(LlmError):
    retryable = True
    code = "LLM_TIMEOUT"


class LlmUpstreamError(LlmError):
    """5xx 或连接层错误。"""

    retryable = True
    code = "LLM_UPSTREAM"


class LlmEmptyContentError(LlmError):
    """200 但 content 为空。

    D2 实测：推理模型的 reasoning token 也计入 max_tokens，预算给小了会出现
    "有 usage 但 content 为空"。这不是重试能解决的问题，要报出来改配置。
    """

    code = "LLM_EMPTY_CONTENT"
