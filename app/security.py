"""治理域安全件：口令散列 + JWT 签发/校验。

两条纪律：
1. **身份不用于授权，但凭证必须校验**（architecture §2 ADR11 的例外说明）：这里管的是
   "你是不是登录了后台"，不是"你是哪种身份"，后者由 widget 侧的声明+在册校验处理。
2. **双链隔离**：admin token 只解 `/admin/*`；widget sessionId 不放行治理域（见 deps）。

口令散列用标准库 PBKDF2-SHA256，不引 bcrypt：少一个编译依赖，够用（迭代 20 万）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from .config import Settings

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 200_000

ADMIN_TOKEN_SCOPE = "admin"


# ---------- 口令 ----------
def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "{alg}${iterations}${salt}${digest}".format(
        alg=PBKDF2_ALGORITHM,
        iterations=iterations,
        salt=base64.b64encode(salt).decode("ascii"),
        digest=base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        alg, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if alg != PBKDF2_ALGORITHM:
            return False
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(digest_text)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations_text)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


# ---------- JWT（D12）----------
def create_admin_token(
    user_id: int, account: str, role: str, settings: Settings
) -> tuple[str, datetime]:
    """签发治理域 token。返回 (token, 过期时间)。"""
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.jwt_ttl_minutes)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "account": account,
        "role": role,
        "scope": ADMIN_TOKEN_SCOPE,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_admin_token(token: str, settings: Settings) -> dict[str, Any]:
    """校验签名、过期与 scope。失败抛 jwt 异常，由依赖层转 401。"""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("scope") != ADMIN_TOKEN_SCOPE:
        raise jwt.InvalidTokenError("token scope 不是治理域")
    return payload
