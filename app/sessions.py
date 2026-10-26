"""widget 会话与身份解析（S1 最小实现；S4 由 modules/identity 接管适配器）。

身份是**声明 + 在册校验**，不是认证（PRD §2.3）：
- 前端上报 source 与 claimedEngineerId 只是"提示"，最终 source 由后端裁决；
- 声明工号在册且启用 → 视为可识别（`engineer_id` 落库）；
- 声明了但不在册 / 没声明 → **不阻断问答**，`engineer_id` 留空，统计归入未识别。
"""

from __future__ import annotations

from typing import Any

import psycopg

from . import users

# 前端允许上报的来源；后端只做裁决，不逐字信任
ALLOWED_SOURCES = ("gateway_sso", "host_injected", "password_login", "anonymous")

# D6：无 SSO，宿主注入是主路径；其余降级
DEFAULT_SOURCE = "host_injected"


def resolve_identity(
    conn: psycopg.Connection,
    *,
    requested_source: str | None,
    claimed_account: str | None,
) -> tuple[str, dict[str, Any] | None]:
    """返回 (最终 source, 命中的用户或 None)。"""
    source = requested_source if requested_source in ALLOWED_SOURCES else DEFAULT_SOURCE
    claimed = (claimed_account or "").strip()

    if not claimed:
        # 没声明工号 → 未识别，但会话照常建立
        return "anonymous", None

    user = users.get_user_by_account(conn, claimed)
    if user and user["status"] == "active" and user["account"] != users.SENTINEL_ACCOUNT:
        # 在册校验通过：来源按声明走（无 SSO 时就是 host_injected）
        if source == "anonymous":
            source = DEFAULT_SOURCE
        return source, user

    # 声明了但不在册 / 已停用：仍记来源，但不算可识别（D9：不自动开通）
    if source == "anonymous":
        source = DEFAULT_SOURCE
    return source, None


def create_session(
    conn: psycopg.Connection,
    *,
    requested_source: str | None,
    claimed_account: str | None,
    host_context: dict[str, Any] | None = None,
    channel: str = "widget",
    demo_code: str | None = None,
) -> dict[str, Any]:
    """建会话并返回契约里的 SessionResponse。

    `demo_code` 只由演示站入口写入（已在校验层确认有效），用于按码统计与限额；
    关闭演示模式时恒为 None，不影响本机开发与测试。
    """
    source, user = resolve_identity(
        conn, requested_source=requested_source, claimed_account=claimed_account
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into sessions
                (auth_source, claimed_engineer_id, engineer_id, channel, host_context, demo_code)
            values (%s, %s, %s, %s, %s::jsonb, %s)
            returning id
            """,
            (
                source,
                claimed_account or None,
                user["id"] if user else None,
                channel,
                psycopg.types.json.Jsonb(host_context or {}),
                demo_code,
            ),
        )
        session_id = int(cur.fetchone()[0])
    conn.commit()

    engineer = _engineer_payload(user, source, claimed_account)
    return {
        "sessionId": f"ses-{session_id}",
        "engineer": engineer,
        "hostContext": _normalize_context(host_context),
    }


_SOURCE_LABELS = {
    "gateway_sso": "网关 SSO",
    "host_injected": "宿主注入",
    "password_login": "密码登录",
    "anonymous": "未识别",
}


def source_label(source: str | None) -> str:
    """身份来源的展示名（沉淀上下文与接口响应共用同一套说法）。"""
    return _SOURCE_LABELS.get(str(source or ""), str(source or "未识别"))


def _engineer_payload(
    user: dict[str, Any] | None, source: str, claimed_account: str | None
) -> dict[str, Any]:
    labels = _SOURCE_LABELS
    return {
        "identified": user is not None,
        "name": user["name"] if user else None,
        "account": user["account"] if user else None,
        "claimed": (claimed_account or None) if user is None else None,
        "source": source,
        "sourceLabel": labels.get(source, source),
    }


def _normalize_context(host_context: dict[str, Any] | None) -> dict[str, Any]:
    """只透传契约里声明的字段；缺的留 None，不做前端补全。"""
    ctx = host_context or {}
    return {
        "ticketNo": ctx.get("ticketNo"),
        "deviceModel": ctx.get("deviceModel"),
        "deviceName": ctx.get("deviceName"),
        "batch": ctx.get("batch"),
    }


def get_session(conn: psycopg.Connection, session_id: str) -> dict[str, Any] | None:
    """读会话（含建立时记录的宿主上下文），供 `/chat` 在请求未带 context 时沿用。"""
    sid = _parse_session_id(session_id)
    if sid is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "select id, auth_source, engineer_id, channel, host_context, demo_code "
            "from sessions where id = %s",
            (sid,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": int(row[0]),
        "auth_source": row[1],
        "engineer_id": row[2],
        "channel": row[3],
        "host_context": row[4] or {},
        "demo_code": row[5],
    }


def session_exists(conn: psycopg.Connection, session_id: str) -> bool:
    return get_session(conn, session_id) is not None


def _parse_session_id(session_id: str) -> int | None:
    """契约里 id 是字符串（ses-12 / 12 都接受）。"""
    raw = session_id.strip()
    if raw.startswith("ses-"):
        raw = raw[4:]
    return int(raw) if raw.isdigit() else None
