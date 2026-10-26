"""初始管理员建档：`python -m app.seed_admin`。

为什么不是迁移里插一条固定口令：**仓库里不能有可用凭据**。
所以账号名与口令都从环境变量读：
- `ADMIN_INITIAL_ACCOUNT`（默认 admin）
- `ADMIN_INITIAL_PASSWORD`（必填；为空则跳过建档，服务照常启动）

幂等：账号已存在只补角色，不重置口令（避免每次重启把管理员口令改回去）。
"""

from __future__ import annotations

import logging
import sys

from . import users
from .config import get_settings
from .db import connect
from .logging_setup import configure_logging

logger = logging.getLogger("app.seed_admin")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.admin_initial_password:
        logger.info("未设置 ADMIN_INITIAL_PASSWORD，跳过初始管理员建档")
        return 0

    with connect() as conn:
        user_id, created = users.ensure_admin(
            conn,
            account=settings.admin_initial_account,
            name=settings.admin_initial_name,
            password=settings.admin_initial_password,
        )
    logger.info(
        "初始管理员%s：%s（id=%s）",
        "已创建" if created else "已存在",
        settings.admin_initial_account,
        user_id,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
