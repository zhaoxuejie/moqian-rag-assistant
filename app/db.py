"""数据库连接与迁移（P0 采用「有序 SQL 迁移」，不引 alembic）。

- 迁移文件：migrations/NNNN_*.sql，按文件名排序执行；
- 已执行记录：schema_migrations 表；
- 幂等：重复执行只跑未应用的版本。
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from .config import get_settings

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().database_url, connect_timeout=5)


def db_healthy() -> bool:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("select 1")
            return cur.fetchone() == (1,)
    except Exception as exc:  # noqa: BLE001 - 健康检查只关心可用性
        logger.warning("db health check failed: %s", exc)
        return False


def apply_migrations() -> list[str]:
    """按序应用未执行的迁移，返回本次应用的版本名。"""
    applied: list[str] = []
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists schema_migrations (
                  version text primary key,
                  applied_at timestamptz not null default now()
                )
                """
            )
            conn.commit()
            cur.execute("select version from schema_migrations")
            done = {row[0] for row in cur.fetchall()}
            for path in files:
                version = path.stem
                if version in done:
                    continue
                logger.info("applying migration %s", version)
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute("insert into schema_migrations(version) values (%s)", (version,))
                conn.commit()
                applied.append(version)
    return applied
