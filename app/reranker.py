"""reranker 客户端装配（与 embedding 一样，app 只做注入，不在 modules 写配置）。"""

from __future__ import annotations

from modules.llm import RerankerClient, RerankerConfig

from .config import Settings


def build_reranker(
    settings: Settings,
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> RerankerClient | None:
    name = str(provider or settings.reranker_provider).strip().lower()
    if name in ("", "none", "off", "disabled"):
        return None
    return RerankerClient(
        RerankerConfig(
            base_url=str(base_url or settings.reranker_base_url).strip(),
            model=str(model or settings.reranker_model).strip(),
            api_key=settings.reranker_api_key,
            timeout_seconds=float(settings.reranker_timeout_seconds),
        )
    )
