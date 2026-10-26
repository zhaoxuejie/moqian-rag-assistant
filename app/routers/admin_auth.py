"""治理域认证路由（D12：JWT HS256）。

- `POST /admin/auth/captcha`：S1 返回 `enabled: false`（图形码形态见契约 Q2，待定）；
- `POST /admin/auth/login`：工号 + 口令 → JWT；
- `GET /admin/me`：需要 Bearer token。
"""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status

from .. import users
from ..branding import read_brand
from ..captcha import captcha_enabled, create_challenge, verify_challenge
from ..config import Settings, get_settings
from ..schemas import LoginRequest
from ..security import create_admin_token, verify_password
from .deps import get_conn, require_admin

router = APIRouter(prefix="/admin", tags=["admin-auth"])

# 登录失败统一文案：不区分"账号不存在"与"口令错误"，避免账号枚举
_LOGIN_FAILED = "工号或口令不正确"


@router.post("/auth/captcha")
def issue_captcha(
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> dict:
    """按 config.security.captchaEnabled 返回一次性图形挑战。"""
    if not captcha_enabled(conn, default=settings.captcha_enabled):
        return {"enabled": False}
    return create_challenge(conn)


@router.get("/auth/brand")
def public_brand(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """登录页公开品牌：只返回名称/组织/主色/Logo 标识，不含任何敏感配置。"""
    return read_brand(conn)


@router.post("/auth/login")
def login(
    payload: LoginRequest,
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> dict:
    """工号 + 口令 → JWT；开启验证码时先校验一次性挑战，再查账号。"""
    if captcha_enabled(conn, default=settings.captcha_enabled):
        if not verify_challenge(
            conn, token=payload.captcha_id, answer=payload.captcha_code
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "CAPTCHA_INVALID",
                    "message": "验证码不正确或已过期，请刷新验证码后重试",
                },
            )
    user = users.get_user_by_account(conn, payload.account)
    if (
        user is None
        or user["account"] == users.SENTINEL_ACCOUNT
        or user["status"] != "active"
        or user["credential"] != "local"
        or not verify_password(payload.password, user["password_hash"])
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": _LOGIN_FAILED},
        )

    token, expires_at = create_admin_token(int(user["id"]), user["account"], user["role"], settings)
    return {
        "token": token,
        "expiresAt": expires_at.isoformat(),
        "user": users.to_public(user),
    }


@router.get("/me")
def me(admin: dict = Depends(require_admin)) -> dict:
    return admin
