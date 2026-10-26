"""演示站门禁与配额（`DEMO_*` 配置；关闭时全部直通）。

两条边界，别混淆：
- **演示码是分流与归因工具**，不是安全边界：公开码写在官网链接里，等于公开；
- **成本闸是配额**：会话轮次 → 演示码日额度（公开码跳过）→ 单 IP 日额度 → 全局日额度。

计数发生在调用 LLM 之前：超限时不写 messages、不调模型，只把 blocked 记到
`demo_usage`。所有判定在同一事务内完成，任一维度超限整体回滚，不会出现
「全局额度被扣了但 IP 额度不够」的错账。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from .config import Settings
from .runtime_config import load_config

# 后台「演示总开关」关闭时的统一话术：说清"入口"这件事，并给出预约出口。
# 文案属行业/运营词汇，理应可配置；本批次先固定在 app 层（widget 代码里不写死）。
PAUSED_TITLE = "演示已暂停"
PAUSED_BODY = (
    "这个演示需要从官网入口进入；当前演示环境暂停开放，暂时不接受提问。"
    "如果需要按你的场景完整评估，可以预约 1:1 演示。"
)


@dataclass(frozen=True)
class QuotaDecision:
    """一次配额判定的结果。`reason` 对应超限维度，命中时 used 为查到的用量。"""

    allowed: bool
    reason: str | None = None  # session | code | ip | global
    turns_used: int = 0
    turns_left: int | None = None


class _QuotaExceeded(Exception):
    def __init__(self, scope: str, subject: str) -> None:
        super().__init__(f"quota exceeded: {scope}")
        self.scope = scope
        self.subject = subject


def client_ip(request: Any, settings: Settings) -> str:
    """取访客 IP。

    反代后 `request.client.host` 是 127.0.0.1，必须看 `X-Forwarded-For`；
    但只有显式信任代理时（`DEMO_TRUST_FORWARDED_FOR=true`）才读它——否则
    任何人都能伪造请求头绕过单 IP 限额。
    """
    if settings.demo_trust_forwarded_for and request is not None:
        forwarded = request.headers.get("x-forwarded-for") or ""
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    if request is not None and getattr(request, "client", None):
        return str(request.client.host)[:64]
    return "unknown"


def verify_access_code(conn: psycopg.Connection, code: str) -> dict[str, Any] | None:
    """校验演示码：存在、启用、未过期。返回码信息（用于归因），否则 None。"""
    normalized = (code or "").strip()
    if not normalized:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "select code, label, channel, enabled, expires_at "
            "from demo_access_codes where code = %s",
            (normalized,),
        )
        row = cur.fetchone()
    if row is None or not row[3]:
        return None
    expires_at = row[4]
    if expires_at is not None and expires_at <= datetime.now(UTC):
        return None
    return {"code": row[0], "label": row[1], "channel": row[2]}


def session_turns(conn: psycopg.Connection, session_pk: int) -> int:
    """已提问轮次（按 user 消息计）：拒答也算一次，避免用拒答刷额度。"""
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from messages where session_id = %s and role = 'user'",
            (session_pk,),
        )
        return int(cur.fetchone()[0])


def demo_paused(conn: psycopg.Connection) -> bool:
    """后台「演示总开关」：`config.demo.enabled = false` 时暂停演示。

    只收窄、不放大——`DEMO_ENABLED=false` 的部署（本机开发）不会被后台开关打开；
    这样"临时关掉助手"不需要改环境变量、也不需要重启进程。
    """
    saved = load_config(conn, ("demo",)).get("demo", {})
    return saved.get("enabled") is False


def demo_state(conn: psycopg.Connection, settings: Settings) -> tuple[bool, bool]:
    """返回 (演示是否开放, 是否被后台暂停)。开放 = 环境变量启用 且 未被暂停。"""
    if not settings.demo_enabled:
        return False, False
    paused = demo_paused(conn)
    return (not paused), paused


def reserve(
    conn: psycopg.Connection,
    *,
    session_pk: int,
    code: str | None,
    ip: str,
    settings: Settings,
) -> QuotaDecision:
    """在调用 LLM 之前预占一次额度；返回是否放行与剩余轮次。"""
    if not settings.demo_enabled:
        return QuotaDecision(allowed=True)

    used = session_turns(conn, session_pk)
    turns_limit = max(0, settings.demo_session_turns)
    turns_left = max(0, turns_limit - used)
    if used >= turns_limit:
        _mark_blocked(conn, "session", f"ses-{session_pk}")
        conn.commit()
        return QuotaDecision(False, "session", used, 0)

    scopes: list[tuple[str, str, int]] = []
    if code and code != settings.demo_public_code:
        scopes.append(("code", code, settings.demo_code_daily))
    scopes.append(("ip", ip, settings.demo_ip_daily))
    scopes.append(("global", "*", settings.demo_global_daily))

    try:
        with conn.transaction():
            for scope, subject, cap in scopes:
                if cap <= 0 or not _bump(conn, scope, subject, cap):
                    raise _QuotaExceeded(scope, subject)
    except _QuotaExceeded as exc:
        _mark_blocked(conn, exc.scope, exc.subject)
        conn.commit()
        return QuotaDecision(False, exc.scope, used, turns_left)

    conn.commit()
    return QuotaDecision(True, None, used, turns_left)


def _bump(conn: psycopg.Connection, scope: str, subject: str, cap: int) -> bool:
    """原子自增并判断是否仍在额度内；返回 False 表示已超限（未写入）。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into demo_usage (day, scope, subject, answered)
            values (current_date, %s, %s, 1)
            on conflict (day, scope, subject) do update
               set answered = demo_usage.answered + 1,
                   updated_at = now()
             where demo_usage.answered < %s
            returning answered
            """,
            (scope, subject, cap),
        )
        return cur.fetchone() is not None


def _mark_blocked(conn: psycopg.Connection, scope: str, subject: str) -> None:
    """记一次被拦（用于运营观察"是被谁拦的"）；失败不影响拒绝本身。"""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into demo_usage (day, scope, subject, blocked)
                values (current_date, %s, %s, 1)
                on conflict (day, scope, subject) do update
                   set blocked = demo_usage.blocked + 1,
                       updated_at = now()
                """,
                (scope, subject[:64]),
            )
    except psycopg.Error:
        conn.rollback()
