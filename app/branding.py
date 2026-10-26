"""品牌配置的默认值与只读投影（登录页与后台共用）。

品牌不是敏感信息：登录页在未登录时也要显示正确的产品名/组织名/主色，
因此这里提供一个公开读取函数；写入口仍只在 `/admin/config` 与 `/admin/theme`。
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

DEFAULT_BRAND: dict[str, str] = {
    "systemName": "知识助手管理台",
    "productName": "售后知识助手",
    "orgName": "陌阡科技",
    "primaryColor": "#0f7a52",
    "logo": "default",
    "logoFileName": "",
}

_KEY = "config.brand"


def read_brand(conn: psycopg.Connection) -> dict[str, str]:
    """已保存品牌覆盖默认值；读失败/无配置时回退默认，不影响登录页。"""
    saved: dict[str, Any] = {}
    try:
        with conn.cursor() as cur:
            cur.execute("select value from app_config where key = %s", (_KEY,))
            row = cur.fetchone()
        if row and isinstance(row[0], dict):
            saved = row[0]
    except Exception as exc:  # noqa: BLE001 - 品牌是展示增强项
        logger.warning("read brand failed: %s", exc)
    return {
        key: str(saved.get(key) or default)
        for key, default in DEFAULT_BRAND.items()
    }
