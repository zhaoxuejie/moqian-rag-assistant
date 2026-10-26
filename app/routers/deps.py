"""路由公共依赖：数据库连接、治理域管理员校验、LLM 客户端。

`get_llm` 是**可覆盖的依赖**：测试用 `app.dependency_overrides` 注入假客户端，
不需要联网就能验证 `/chat` 的事件顺序与错误分支。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import jwt
import psycopg
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from modules.llm import EmbeddingClient, LlmClient, config_from_settings

from .. import users
from ..config import Settings, get_settings
from ..db import connect
from ..embedding import get_embedding_client
from ..reranker import build_reranker
from ..runtime_config import load_config
from ..security import decode_admin_token

_bearer = HTTPBearer(auto_error=False)


def get_conn() -> Iterator[psycopg.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def get_llm(
    settings: Settings = Depends(get_settings),
    conn: psycopg.Connection = Depends(get_conn),
) -> LlmClient:
    """LLM 客户端：环境变量为底，`app_config` 的 llm 组覆盖（D-012）。"""
    config = config_from_settings(settings)
    overrides = load_config(conn, ("llm",)).get("llm", {})
    changes: dict[str, Any] = {}
    if overrides.get("baseUrl"):
        changes["base_url"] = str(overrides["baseUrl"]).strip()
    if overrides.get("model"):
        changes["model"] = str(overrides["model"]).strip()
    if overrides.get("timeoutSec"):
        try:
            changes["timeout_seconds"] = float(overrides["timeoutSec"])
        except (TypeError, ValueError):
            pass
    if changes:
        config = replace(config, **changes)
    return LlmClient(config)


def get_embedder(
    settings: Settings = Depends(get_settings),
    conn: psycopg.Connection = Depends(get_conn),
) -> EmbeddingClient:
    """查询向量化客户端：环境变量为底，`app_config` 的 embedding 组覆盖 baseUrl/model。

    注意：不覆盖 `dim` —— 维度与 `chunks.embedding vector(1024)` 绑定，
    改维度必须重新向量化全部证据单元，不能在请求路径里静默切换。
    测试里用假 embedder 覆盖，避免依赖本机 Ollama。
    """
    overrides = load_config(conn, ("embedding",)).get("embedding", {})
    updates: dict[str, Any] = {}
    if overrides.get("baseUrl"):
        updates["embedding_base_url"] = str(overrides["baseUrl"]).strip()
    if overrides.get("model"):
        updates["embedding_model"] = str(overrides["model"]).strip()
    if updates:
        settings = settings.model_copy(update=updates)
    return get_embedding_client(settings)


def get_reranker(
    settings: Settings = Depends(get_settings),
    conn: psycopg.Connection = Depends(get_conn),
):
    """cross-encoder 客户端：provider=none 时返回 None，检索回退 RRF。

    `app_config.config.reranker` 可覆盖 baseUrl/model/provider（密钥只从环境变量读）。
    """
    overrides = load_config(conn, ("reranker",)).get("reranker", {})
    return build_reranker(
        settings,
        provider=overrides.get("provider") or settings.reranker_provider,
        base_url=overrides.get("baseUrl") or settings.reranker_base_url,
        model=overrides.get("model") or settings.reranker_model,
    )


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "UNAUTHORIZED", "message": message},
    )


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """治理域守卫：校验 JWT + 账号仍在册。

    注意：这里校验的是"登录态"，不是"身份"；工程师侧 sessionId 进不来（双链隔离）。
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized("请先登录")
    try:
        payload = decode_admin_token(credentials.credentials, settings)
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise _unauthorized("登录凭证无效") from exc

    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError) as exc:
        raise _unauthorized("登录凭证缺少用户") from exc

    user = users.get_user_by_id(conn, user_id)
    if user is None or user["status"] != "active":
        raise _unauthorized("账号不存在或已停用")
    return users.to_public(user)


def require_governance_admin(
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """治理域角色边界：登录校验之外，只多一条"必须是管理员"。

    身份/角色只用于治理域访问与统计归属，不进入 Widget 身份链（双链隔离）。
    """
    if admin.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "该功能仅管理员可见"},
        )
    return admin
