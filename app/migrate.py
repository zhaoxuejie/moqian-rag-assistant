"""开发/容器启动时应用迁移：python -m app.migrate"""

from __future__ import annotations

import logging

from .db import apply_migrations

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    applied = apply_migrations()
    if applied:
        print("applied:", ", ".join(applied))
    else:
        print("no pending migrations")
