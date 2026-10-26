"""后台「用户管理 + 我的账号」接口（页面 users / account）。

口径（见 contracts/openapi.yaml）：
- **账号即工号**：全平台唯一、不可改、不可复用（architecture §2 ADR11）；人员变动只能
  "禁用旧号 + 建新号"，历史日志按工号归属，指标才可追溯。
- **两级角色、不做 RBAC**：治理域接口要求 `role=admin`（普通用户 403），个人域接口任何
  在册账号可用。`__unidentified__` 是归属哨兵，不进列表、不可登录、不可修改。
- **删除 = 软删**（契约 `/admin/users/{userId}` delete）：只把 `status` 置 disabled，
  历史问答归属保留；不物理删除任何账号行。
- **待开通工号**：`identity_pending` 与"宿主声明过但不在册"的会话聚合并集；开通只补账号，
  不改历史会话归属（避免指标被追溯篡改）。
- **口令**：只在 `users.password_hash` 存 PBKDF2 散列；重置口令只在响应里返回一次，
  不写日志、不落明文。
"""

from __future__ import annotations

import re
import secrets
from datetime import date, datetime
from typing import Any

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from psycopg import errors as pg_errors
from psycopg.rows import dict_row

from .. import users
from ..security import hash_password, verify_password
from .deps import get_conn, require_admin, require_governance_admin

router = APIRouter(prefix="/admin", tags=["admin-governance"])

_RESERVED_ACCOUNT = users.SENTINEL_ACCOUNT
_ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,20}$")
_MIN_PASSWORD_LENGTH = 6
_TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
_UNSET: Any = object()


# ---------------------------------------------------------------- 公共小件
def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _not_found(message: str = "资源不存在") -> HTTPException:
    return _error(status.HTTP_404_NOT_FOUND, "NOT_FOUND", message)


def _conflict(message: str) -> HTTPException:
    return _error(status.HTTP_409_CONFLICT, "CONFLICT", message)


def _invalid(message: str) -> HTTPException:
    return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "VALIDATION_ERROR", message)


def _fmt_time(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M") if value else None


def _pagination(page: int, page_size: int, total: int) -> dict[str, Any]:
    return {"page": page, "pageSize": page_size, "total": total}


def _text(payload: dict[str, Any], key: str, label: str, *, max_length: int = 64) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"请填写{label}")
    text = value.strip()
    if len(text) > max_length:
        raise _invalid(f"{label}不能超过 {max_length} 个字符")
    return text


def _optional_text(
    payload: dict[str, Any], key: str, *, max_length: int = 64
) -> Any:
    """可选文本字段：缺省返回 `_UNSET`（区分"没传"与"传 null"），空串归一为 None。"""
    if key not in payload:
        return _UNSET
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid(f"{key} 必须是字符串")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise _invalid(f"{key} 不能超过 {max_length} 个字符")
    return text


def _role(payload: dict[str, Any]) -> str:
    role = payload.get("role", "user")
    if role not in ("admin", "user"):
        raise _invalid("角色只能是 admin 或 user")
    return str(role)


def _credential(payload: dict[str, Any]) -> str:
    credential = payload.get("credential", "local")
    if credential not in ("domain", "local"):
        raise _invalid("凭据来源只能是 domain 或 local")
    return str(credential)


def _parse_user_id(raw: str) -> int:
    text = (raw or "").strip()
    if text.startswith("u-"):
        text = text[2:]
    if not text.isdigit():
        raise _not_found("用户不存在")
    return int(text)


def _generate_temp_password() -> str:
    """一次性临时口令，形如 `Ab3d-Xk9m`（与后台原型一致，只在响应里出现一次）。"""
    raw = "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _account_exists(conn: psycopg.Connection, account: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("select 1 from users where lower(account) = lower(%s)", (account,))
        return cur.fetchone() is not None


def _other_active_admins(conn: psycopg.Connection, exclude_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from users where role = 'admin' and status = 'active' and id <> %s",
            (exclude_id,),
        )
        return int(cur.fetchone()[0])


def _admin_user(row: dict[str, Any]) -> dict[str, Any]:
    payload = users.to_public(row)
    payload["lastActive"] = _fmt_time(row.get("last_active"))
    return payload


_ADMIN_USER_SELECT = """
select u.id, u.account, u.name, u.role, u.status, u.credential, u.password_hash,
       u.group_name,
       (select max(s.created_at) from sessions s where s.engineer_id = u.id) as last_active
  from users u
"""


def _admin_user_by_id(conn: psycopg.Connection, user_id: int) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_ADMIN_USER_SELECT + " where u.id = %s", (user_id,))
        row = cur.fetchone()
    if row is None:
        raise _not_found("用户不存在")
    return _admin_user(row)


# ---------------------------------------------------------------- 用户列表 / 新建
@router.get("/users", dependencies=[Depends(require_governance_admin)])
def list_users(
    q: str | None = None,
    role: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """账号列表：排除未识别哨兵；`lastActive` 取自该账号最近一次会话时间。

    过滤条件（角色 / 状态 / 关键词）与 `pagination.total` 同源，前端"共 N 条"不会漂。
    """
    if role is not None and role not in ("admin", "user"):
        raise _invalid("角色只能是 admin 或 user")
    if status_filter is not None and status_filter not in ("active", "disabled"):
        raise _invalid("状态只能是 active 或 disabled")

    where = ["u.account <> %s"]
    params: list[Any] = [_RESERVED_ACCOUNT]
    if role:
        where.append("u.role = %s")
        params.append(role)
    if status_filter:
        where.append("u.status = %s")
        params.append(status_filter)
    if q:
        where.append(
            "(u.name ilike %s or u.account ilike %s or coalesce(u.group_name, '') ilike %s)"
        )
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    clause = " and ".join(where)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"select count(*) as total from users u where {clause}", params)
        total = int(cur.fetchone()["total"])
        cur.execute(
            _ADMIN_USER_SELECT
            + f" where {clause} order by u.id limit %s offset %s",
            [*params, page_size, (page - 1) * page_size],
        )
        rows = cur.fetchall()

    return {
        "items": [_admin_user(row) for row in rows],
        "pagination": _pagination(page, page_size, total),
    }


@router.post(
    "/users", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_governance_admin)]
)
def create_user(
    payload: dict[str, Any] = Body(...),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """新建账号：工号唯一且建号后不可改；`local` 凭据必须带初始口令（散列存储）。"""
    account = _text(payload, "account", "工号", max_length=20)
    if not _ACCOUNT_PATTERN.fullmatch(account):
        raise _invalid("工号用 3–20 位英文、数字、. _ -")
    if account == _RESERVED_ACCOUNT:
        raise _invalid("该工号是系统保留标识，不能作为账号")

    name = _text(payload, "name", "姓名", max_length=64)
    role = _role(payload)
    credential = _credential(payload)
    team = _optional_text(payload, "team")
    team_value = None if team is _UNSET else team

    password_hash: str | None = None
    if credential == "local":
        password = payload.get("password")
        if not isinstance(password, str) or len(password) < _MIN_PASSWORD_LENGTH:
            raise _invalid(f"平台密码账号必须设置至少 {_MIN_PASSWORD_LENGTH} 位初始密码")
        if len(password) > 200:
            raise _invalid("初始密码过长")
        password_hash = hash_password(password)

    if _account_exists(conn, account):
        raise _conflict(f"工号「{account}」已存在，账号不可复用")

    try:
        user_id = users.create_user(
            conn,
            account=account,
            name=name,
            role=role,
            credential=credential,
            password_hash=password_hash,
            group_name=team_value,
        )
    except pg_errors.UniqueViolation as exc:  # 并发下唯一约束兜底
        conn.rollback()
        raise _conflict(f"工号「{account}」已存在，账号不可复用") from exc

    return _admin_user_by_id(conn, user_id)


# ---------------------------------------------------------------- 编辑 / 停用启用 / 删除
@router.patch("/users/{user_id}")
def patch_user(
    user_id: str,
    payload: dict[str, Any] = Body(...),
    conn: psycopg.Connection = Depends(get_conn),
    admin: dict[str, Any] = Depends(require_governance_admin),
) -> dict[str, Any]:
    """改姓名 / 小组 / 角色 / 启停用。`account` 与 `credential` 不在可改范围。"""
    target_id = _parse_user_id(user_id)
    current = users.get_user_by_id(conn, target_id)
    if current is None:
        raise _not_found("用户不存在")
    if current["account"] == _RESERVED_ACCOUNT:
        raise _conflict("未识别哨兵账号不可修改")
    if "account" in payload:
        raise _invalid("工号是账号唯一标识，不可修改")

    columns: list[str] = []
    params: list[Any] = []
    if "name" in payload:
        columns.append("name = %s")
        params.append(_text(payload, "name", "姓名", max_length=64))
    if "team" in payload:
        team = _optional_text(payload, "team")
        columns.append("group_name = %s")
        params.append(None if team is _UNSET else team)
    if "role" in payload:
        columns.append("role = %s")
        params.append(_role(payload))
    if "status" in payload:
        next_status = payload.get("status")
        if next_status not in ("active", "disabled"):
            raise _invalid("状态只能是 active 或 disabled")
        columns.append("status = %s")
        params.append(next_status)

    next_role = payload["role"] if "role" in payload else current["role"]
    next_status = payload["status"] if "status" in payload else current["status"]
    if target_id == int(admin["id"]) and (next_role != "admin" or next_status != "active"):
        raise _conflict("不能修改自己的角色或停用当前登录账号，请让另一位管理员操作")
    losing_admin = current["role"] == "admin" and current["status"] == "active" and (
        next_role != "admin" or next_status != "active"
    )
    if losing_admin and _other_active_admins(conn, target_id) == 0:
        raise _conflict("不能把最后一个管理员降级或停用")

    if columns:
        columns.append("updated_at = now()")
        with conn.cursor() as cur:
            cur.execute(
                f"update users set {', '.join(columns)} where id = %s", [*params, target_id]
            )
        conn.commit()

    return _admin_user_by_id(conn, target_id)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_governance_admin)],
)
def delete_user(
    user_id: str,
    conn: psycopg.Connection = Depends(get_conn),
    admin: dict[str, Any] = Depends(require_admin),
) -> Response:
    """删除 = 软删除：只置 `status=disabled`，历史问答与评测归属保留。"""
    target_id = _parse_user_id(user_id)
    current = users.get_user_by_id(conn, target_id)
    if current is None:
        raise _not_found("用户不存在")
    if current["account"] == _RESERVED_ACCOUNT:
        raise _conflict("未识别哨兵账号不可删除")
    if target_id == int(admin["id"]):
        raise _conflict("不能删除当前登录的账号")
    if (
        current["role"] == "admin"
        and current["status"] == "active"
        and _other_active_admins(conn, target_id) == 0
    ):
        raise _conflict("不能删除最后一个管理员")

    with conn.cursor() as cur:
        cur.execute(
            "update users set status = 'disabled', updated_at = now() where id = %s",
            (target_id,),
        )
    conn.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/users/{user_id}/reset-password", dependencies=[Depends(require_governance_admin)]
)
def reset_user_password(
    user_id: str,
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """管理员重置本地账号口令：生成一次性临时口令并散列存储，明文只在本次响应出现。"""
    target_id = _parse_user_id(user_id)
    current = users.get_user_by_id(conn, target_id)
    if current is None:
        raise _not_found("用户不存在")
    if current["account"] == _RESERVED_ACCOUNT:
        raise _conflict("未识别哨兵账号不可重置口令")
    if current["credential"] != "local":
        raise _error(
            status.HTTP_409_CONFLICT,
            "DOMAIN_ACCOUNT",
            "域账号口令由企业域管理，平台不能重置",
        )

    temporary_password = _generate_temp_password()
    with conn.cursor() as cur:
        cur.execute(
            "update users set password_hash = %s, updated_at = now() where id = %s",
            (hash_password(temporary_password), target_id),
        )
    conn.commit()
    return {"ok": True, "temporaryPassword": temporary_password}


# ---------------------------------------------------------------- 待开通工号
def _session_claims(conn: psycopg.Connection) -> dict[str, dict[str, Any]]:
    """宿主声明过、但平台没有对应账号的工号：按声明次数与最近时间聚合。"""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select s.claimed_engineer_id as account,
                   count(*) as cnt,
                   max(s.created_at) as last_at
              from sessions s
              left join users u on u.account = s.claimed_engineer_id
             where s.claimed_engineer_id is not null
               and s.engineer_id is null
               and u.id is null
             group by s.claimed_engineer_id
            """
        )
        return {row["account"]: row for row in cur.fetchall()}


def _pending_items(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """待开通清单 = identity_pending ∪ 会话声明聚合（两者都排除已开通账号）。

    契约口径是按 `sessions.claimed_engineer_id` 聚合；`identity_pending` 是治理域维护的
    待办表（0004）。两个来源取并集，管理员的「开通」入口在两种数据下都有效。
    """
    claims = _session_claims(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("select account, last_seen_at from identity_pending")
        pending = cur.fetchall()
        cur.execute("select account from users")
        registered = {row["account"] for row in cur.fetchall()}

    merged: dict[str, dict[str, Any]] = {}
    for row in pending:
        account = row["account"]
        if account in registered:
            continue
        claim = claims.pop(account, None)
        last_seen = row["last_seen_at"]
        if claim and claim["last_at"] and (last_seen is None or claim["last_at"] > last_seen):
            last_seen = claim["last_at"]
        merged[account] = {
            "account": account,
            "count": int(claim["cnt"]) if claim else 0,
            "lastAt": _fmt_time(last_seen),
        }

    for account, claim in claims.items():
        if account in registered:
            continue
        merged[account] = {
            "account": account,
            "count": int(claim["cnt"]),
            "lastAt": _fmt_time(claim["last_at"]),
        }

    return sorted(merged.values(), key=lambda item: (-item["count"], item["account"]))


@router.get("/identity/pending", dependencies=[Depends(require_governance_admin)])
def list_pending_identity(conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    return {"items": _pending_items(conn)}


@router.post(
    "/identity/pending/{account}/activate",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_governance_admin)],
)
def activate_pending_account(
    account: str,
    payload: dict[str, Any] = Body(...),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """把声明工号开通为在册账号；历史会话归属保持不变（不追溯改写）。"""
    account = account.strip()
    if not account or account == _RESERVED_ACCOUNT:
        raise _not_found("该工号不在待开通清单")
    if _account_exists(conn, account):
        raise _conflict(f"工号「{account}」已开通")
    if account not in {item["account"] for item in _pending_items(conn)}:
        raise _not_found("该工号不在待开通清单")

    name = _text(payload, "name", "姓名", max_length=64)
    role = _role(payload)
    credential = _credential(payload)
    team = _optional_text(payload, "team")
    team_value = None if team is _UNSET else team
    # 契约里开通请求不带口令：local 账号先落一个随机散列，管理员随后走
    # /reset-password 拿到一次性临时口令（前端开通流程会自动做这一步）。
    password_hash = hash_password(_generate_temp_password()) if credential == "local" else None

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into users
                    (account, name, role, status, credential, password_hash, group_name)
                values (%s, %s, %s, 'active', %s, %s, %s)
                returning id
                """,
                (account, name, role, credential, password_hash, team_value),
            )
            user_id = int(cur.fetchone()[0])
            cur.execute("delete from identity_pending where account = %s", (account,))
        conn.commit()
    except pg_errors.UniqueViolation as exc:
        conn.rollback()
        raise _conflict(f"工号「{account}」已开通") from exc

    return _admin_user_by_id(conn, user_id)


# ---------------------------------------------------------------- 我的账号（个人域）
@router.put("/me/password")
def change_my_password(
    payload: dict[str, Any] = Body(...),
    conn: psycopg.Connection = Depends(get_conn),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """修改本人口令：必须校验旧口令；域账号由 AD 域管（409 DOMAIN_ACCOUNT）。"""
    current = users.get_user_by_id(conn, int(admin["id"]))
    if current is None:
        raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "登录已失效，请重新登录")
    if current["credential"] != "local":
        raise _error(
            status.HTTP_409_CONFLICT,
            "DOMAIN_ACCOUNT",
            "域账号口令由企业域账号管理，本平台不保存密码",
        )

    old_password = payload.get("oldPassword")
    new_password = payload.get("newPassword")
    if not isinstance(old_password, str) or not old_password:
        raise _invalid("请输入当前密码")
    if not verify_password(old_password, current["password_hash"]):
        # 不返回 401：登录态仍然有效，401 会被前端当成"会话失效"踢回登录页。
        raise _error(status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", "当前密码不正确")
    if not isinstance(new_password, str) or len(new_password) < _MIN_PASSWORD_LENGTH:
        raise _invalid(f"新密码至少 {_MIN_PASSWORD_LENGTH} 位")
    if len(new_password) > 200:
        raise _invalid("新密码过长")
    if new_password == old_password:
        raise _invalid("新密码不能与当前密码相同")

    with conn.cursor() as cur:
        cur.execute(
            "update users set password_hash = %s, updated_at = now() where id = %s",
            (hash_password(new_password), current["id"]),
        )
    conn.commit()
    return {"ok": True}


_QA_MESSAGE_SELECT = """
select m.id as message_id,
       m.session_id,
       m.content,
       m.structured_query,
       m.created_at,
       s.auth_source,
       s.claimed_engineer_id,
       s.channel,
       s.host_context,
       a.id as answer_id,
       a.refused,
       a.gate_reason,
       a.citations,
       a.resolved,
       a.question_at,
       a.resolved_at,
       exists (select 1 from handoffs h where h.session_id = m.session_id) as handoff
  from messages m
  join sessions s on s.id = m.session_id
  left join lateral (
        select a.id, a.refused, a.gate_reason, a.citations, a.resolved,
               a.question_at, a.resolved_at
          from messages a
         where a.session_id = m.session_id
           and a.role = 'assistant'
           and a.created_at >= m.created_at
         order by a.created_at asc
         limit 1
  ) a on true
"""


def _outcome(row: dict[str, Any]) -> str:
    if row.get("answer_id") is None:
        return "error"
    gate = row.get("gate_reason")
    if gate == "safe":
        return "safe"
    if gate == "error":
        return "error"
    if row.get("refused"):
        return "gap"
    return "answered"


def _resolved(row: dict[str, Any]) -> str | None:
    if row.get("answer_id") is None:
        return None
    value = row.get("resolved")
    if value is True:
        return "resolved"
    if value is False:
        return "unresolved"
    return None


def _hits(citations: list[Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for item in citations:
        if not isinstance(item, dict):
            continue
        hits.append(
            {
                "evidenceId": item.get("evidenceId"),
                "loc": item.get("loc"),
                "kind": item.get("kind"),
                "score": item.get("score"),
            }
        )
    return hits


def _qa_message(row: dict[str, Any], account: str, name: str | None) -> dict[str, Any]:
    citations = row.get("citations") or []
    host_context = row.get("host_context") or {}
    resolved_in_sec = None
    if row.get("resolved_at") and row.get("question_at"):
        resolved_in_sec = max(
            0, int((row["resolved_at"] - row["question_at"]).total_seconds())
        )
    message: dict[str, Any] = {
        "id": f"msg-{row['message_id']}",
        "time": _fmt_time(row.get("created_at")),
        "sessionId": f"ses-{row['session_id']}",
        "user": name,
        "ticket": host_context.get("ticketNo"),
        "identity": {
            "source": row.get("auth_source") or "anonymous",
            "claimed": row.get("claimed_engineer_id"),
            "engineer": account,
        },
        "question": row.get("content") or "",
        "hits": _hits(citations),
        "citations": len(citations),
        "outcome": _outcome(row),
        "resolved": _resolved(row),
        "resolvedInSec": resolved_in_sec,
        "handoff": bool(row.get("handoff")),
        "channel": row.get("channel") or "widget",
    }
    if row.get("structured_query"):
        message["structured"] = row["structured_query"]
    return message


@router.get("/me/messages")
def my_messages(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    conn: psycopg.Connection = Depends(get_conn),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """我的问答记录：只返回本人会话、排除管理员自测（`admin_preview`）。"""
    where = ["m.role = 'user'", "s.engineer_id = %s", "s.channel <> 'admin_preview'"]
    params: list[Any] = [int(admin["id"])]
    if date_from:
        where.append("m.created_at >= %s::date")
        params.append(date_from)
    if date_to:
        where.append("m.created_at < (%s::date + interval '1 day')")
        params.append(date_to)
    clause = " and ".join(where)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select count(*) as total from messages m join sessions s on s.id = m.session_id "
            f"where {clause}",
            params,
        )
        total = int(cur.fetchone()["total"])
        cur.execute(
            _QA_MESSAGE_SELECT
            + f" where {clause} order by m.created_at desc limit %s offset %s",
            [*params, page_size, (page - 1) * page_size],
        )
        rows = cur.fetchall()

    items = [_qa_message(row, admin["account"], admin.get("name")) for row in rows]
    return {
        "items": items,
        "pagination": _pagination(page, page_size, total),
        "stats": {"rounds": len(items), "identifiableRounds": len(items)},
    }


_SAVED_SELECT = """
select m.id as message_id,
       m.created_at,
       m.citations,
       q.content as question
  from messages m
  join sessions s on s.id = m.session_id
  left join lateral (
        select content
          from messages question
         where question.session_id = m.session_id
           and question.role = 'user'
           and question.created_at <= m.created_at
         order by question.created_at desc
         limit 1
  ) q on true
 where m.role = 'assistant'
   and m.feedback = 'saved'
   and s.engineer_id = %s
   and s.channel <> 'admin_preview'
"""


@router.get("/me/saved")
def my_saved(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    conn: psycopg.Connection = Depends(get_conn),
    admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    """我的收藏：本人会话里 `feedback='saved'` 的回答，问题取同会话上一条提问。"""
    params: list[Any] = [int(admin["id"])]
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "select count(*) as total from messages m join sessions s on s.id = m.session_id "
            "where m.role = 'assistant' and m.feedback = 'saved' and s.engineer_id = %s "
            "and s.channel <> 'admin_preview'",
            params,
        )
        total = int(cur.fetchone()["total"])
        cur.execute(
            _SAVED_SELECT + " order by m.created_at desc limit %s offset %s",
            [*params, page_size, (page - 1) * page_size],
        )
        rows = cur.fetchall()

    items = [
        {
            "id": f"saved-{row['message_id']}",
            "question": row.get("question") or "",
            "messageId": f"msg-{row['message_id']}",
            "savedAt": _fmt_time(row.get("created_at")),
            "citations": row.get("citations") or [],
        }
        for row in rows
    ]
    return {"items": items, "pagination": _pagination(page, page_size, total)}
