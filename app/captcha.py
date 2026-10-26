"""登录图形验证码（D-012 security 组接线）。

- 一次性挑战存 DB：只存答案哈希与过期时间，不存明文；
- 返回 SVG data URI（服务端生成，无外部依赖），登录时回传 token + code；
- 过期/已用/不匹配都返回 False；验证成功后立即标记 used。
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any

import psycopg

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉易混淆的 I/O/0/1
TTL_SECONDS = 300


def _answer_hash(code: str, token: str) -> str:
    return hashlib.sha256(f"{code.strip().lower()}:{token}".encode()).hexdigest()


def _svg(code: str) -> str:
    # 简单但真实的图形码：字符、干扰线、轻微旋转；不引入第三方依赖
    chars = []
    for index, char in enumerate(code):
        x = 16 + index * 30
        rotate = (-12, 9, -7, 11)[index % 4]
        chars.append(
            f'<text x="{x}" y="38" transform="rotate({rotate} {x} 32)" '
            f'font-family="monospace" font-size="30" font-weight="700" '
            f'fill="#26313c">{char}</text>'
        )
    lines = "".join(
        f'<path d="M0 {y} L120 {y + dy}" stroke="#8a97a3" stroke-width="1" opacity=".55"/>'
        for y, dy in ((12, -5), (25, 7), (44, -3), (54, 4))
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60" '
        'viewBox="0 0 120 60"><rect width="120" height="60" rx="6" fill="#f4f6f8"/>'
        + lines
        + "".join(chars)
        + "</svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def create_challenge(conn: psycopg.Connection) -> dict[str, Any]:
    """生成挑战并落库；返回前端的 image/token/expiresInSec。"""
    token = secrets.token_urlsafe(24)
    code = "".join(secrets.choice(ALPHABET) for _ in range(4))
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into captcha_challenges(token, answer_hash, expires_at)
            values (%s, %s, now() + make_interval(secs => %s))
            """,
            (token, _answer_hash(code, token), TTL_SECONDS),
        )
        # 顺手清理过期挑战，避免表无限增长
        cur.execute(
            "delete from captcha_challenges where expires_at < now() - interval '1 hour'"
        )
    conn.commit()
    return {
        "enabled": True,
        "captchaId": token,
        "image": _svg(code),
        "expiresInSec": TTL_SECONDS,
    }


def verify_challenge(
    conn: psycopg.Connection, *, token: str | None, answer: str | None
) -> bool:
    """一次性校验；无论成功失败，已过期/已用/不匹配都 False。"""
    if not token or not answer or not answer.strip():
        return False
    with conn.cursor() as cur:
        cur.execute(
            "select answer_hash, expires_at > now() as alive, used "
            "from captcha_challenges where token = %s",
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return False
        answer_hash, alive, used = row
        if not alive or used:
            return False
        expected = str(answer_hash)
        ok = secrets.compare_digest(expected, _answer_hash(answer, token))
        if ok:
            cur.execute(
                "update captcha_challenges set used = true where token = %s", (token,)
            )
    conn.commit()
    return bool(ok)


def captcha_enabled(conn: psycopg.Connection, default: bool = False) -> bool:
    """读取 app_config.config.security.captchaEnabled；读失败回退默认。"""
    try:
        with conn.cursor() as cur:
            cur.execute("select value from app_config where key = 'config.security'")
            row = cur.fetchone()
        if row and isinstance(row[0], dict):
            return bool(row[0].get("captchaEnabled", default))
    except Exception:  # noqa: BLE001 - 验证码是防护项，读配置失败回退默认
        pass
    return default
