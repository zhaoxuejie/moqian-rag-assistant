"""用户与在册校验（app 层的数据访问）。

为什么放在 app 而不放 modules：账号字段名（工号、小组、域账号）是**行业/客户词汇**，
换客户可能连表结构含义都变；`modules/identity` 只保留"来源分层 + 降级"的机制。

约定：账号即工号，**不可改、不可复用**（architecture §2 ADR11）；`__unidentified__`
是归属哨兵，不可登录。
"""

from __future__ import annotations

from typing import Any

import psycopg

SENTINEL_ACCOUNT = "__unidentified__"

_USER_COLUMNS = "id, account, name, role, status, credential, password_hash, group_name"


def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    keys = ("id", "account", "name", "role", "status", "credential", "password_hash", "group_name")
    return dict(zip(keys, row, strict=True))


def get_user_by_account(conn: psycopg.Connection, account: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(f"select {_USER_COLUMNS} from users where account = %s", (account,))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def get_user_by_id(conn: psycopg.Connection, user_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(f"select {_USER_COLUMNS} from users where id = %s", (user_id,))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def to_public(user: dict[str, Any]) -> dict[str, Any]:
    """转成契约里的 AdminUser（camelCase，不含口令散列）。"""
    return {
        "id": str(user["id"]),
        "account": user["account"],
        "name": user["name"],
        "role": user["role"],
        "status": user["status"],
        "team": user.get("group_name"),
        "credential": user["credential"],
        "lastActive": None,
        "isSentinel": user["account"] == SENTINEL_ACCOUNT,
    }


def create_user(
    conn: psycopg.Connection,
    *,
    account: str,
    name: str,
    role: str = "user",
    credential: str = "local",
    password_hash: str | None = None,
    group_name: str | None = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into users (account, name, role, status, credential, password_hash, group_name)
            values (%s, %s, %s, 'active', %s, %s, %s)
            returning id
            """,
            (account, name, role, credential, password_hash, group_name),
        )
        user_id = int(cur.fetchone()[0])
    conn.commit()
    return user_id


def ensure_admin(
    conn: psycopg.Connection,
    *,
    account: str,
    name: str,
    password: str,
) -> tuple[int, bool]:
    """保证存在一个可登录的管理员：返回 (id, 是否新建)。

    幂等：已存在则只补角色，不改密码（避免每次重启重置管理员口令）。
    """
    existing = get_user_by_account(conn, account)
    if existing:
        if existing["role"] != "admin" or existing["status"] != "active":
            with conn.cursor() as cur:
                cur.execute(
                    "update users set role = 'admin', status = 'active', updated_at = now() "
                    "where id = %s",
                    (existing["id"],),
                )
            conn.commit()
        return int(existing["id"]), False

    from .security import hash_password

    user_id = create_user(
        conn,
        account=account,
        name=name,
        role="admin",
        credential="local",
        password_hash=hash_password(password),
    )
    return user_id, True
