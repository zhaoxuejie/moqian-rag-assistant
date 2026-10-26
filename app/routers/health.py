"""探活路由：`/health` 不依赖数据库（容器与 CI 用），`/health/db` 查连通性。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..db import db_healthy

router = APIRouter(tags=["ops"])


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    """探活 + 版本信息。

    `version` / `build_sha` 不加鉴权：部署脚本靠它验证「跑起来的到底是不是这一版」，
    管理台也靠它显示版本。这里没有任何密钥类信息，公网可见没有风险。
    """
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.version,
        "env": settings.app_env,
        # 给人看的环境名（本地环境 / 生产环境），由 .env 的 APP_ENV_LABEL 决定；
        # 判定逻辑永远用 env，不用这个标签。
        "envLabel": settings.app_env_label,
        # 接口字段一律 camelCase（契约 test_openapi_contract 会拦）；
        # 配置侧仍是 snake_case 的 build_sha / build_time
        "buildSha": settings.build_sha,
        "buildTime": settings.build_time,
    }


@router.get("/health/db")
def health_db() -> dict:
    ok = db_healthy()
    return {"status": "ok" if ok else "degraded", "database": "up" if ok else "down"}
