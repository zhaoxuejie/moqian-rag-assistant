"""embedding 客户端装配（app → modules 单向依赖的注入点）。

D14：本机 Ollama 暴露 OpenAI 兼容端点，`bge-m3:latest` 返回 1024 维，
与 `chunks.embedding vector(1024)` 对齐。
容器内访问宿主机用 `host.docker.internal:11434`；本地直跑用 `localhost:11434`。
"""

from __future__ import annotations

from modules.llm import EmbeddingClient, EmbeddingConfig

from .config import Settings


def build_embedding_config(settings: Settings) -> EmbeddingConfig:
    return EmbeddingConfig(
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dim=settings.embedding_dim,
        api_key=settings.embedding_api_key,
        timeout_seconds=settings.embedding_timeout_seconds,
        batch_size=settings.embedding_batch_size,
    )


def get_embedding_client(settings: Settings) -> EmbeddingClient:
    return EmbeddingClient(build_embedding_config(settings))
