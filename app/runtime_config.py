"""从 `app_config` 读取运行时覆盖（S7 / D-012）。

边界：
- 只读，不在请求路径写配置；写入仍只发生在 `/admin/config`。
- 只覆盖有明确消费点的组：llm / retrieval / safety / widget / brand。
- 读取失败（表不存在、DB 抖动、值非法）一律回退环境变量，不阻断问答。
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

GROUP_PREFIX = "config."


def load_config(
    conn: psycopg.Connection, groups: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    """读取 `config.<group>` 的 JSON 对象；失败返回空 dict。"""
    keys = [GROUP_PREFIX + group for group in groups]
    try:
        with conn.cursor() as cur:
            cur.execute("select key, value from app_config where key = any(%s)", (keys,))
            rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 - 配置是增强项，不能阻断主链路
        logger.warning("load runtime config failed: %s", exc)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in rows:
        group = str(key)[len(GROUP_PREFIX) :]
        if group in groups and isinstance(value, dict):
            out[group] = dict(value)
    return out


def _number(data: dict[str, Any], key: str, default: Any, cast) -> Any:
    value = data.get(key)
    if value is None or value == "":
        return default
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default
