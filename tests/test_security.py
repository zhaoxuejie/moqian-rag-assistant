"""安全件单测：口令散列与 JWT（D12）。

要点：
- 口令散列必须带随机盐（同一个口令两次散列结果不同）；
- JWT 校验必须同时管签名、过期、scope（防止 widget 侧 token 混进治理域）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import Settings
from app.security import (
    create_admin_token,
    decode_admin_token,
    hash_password,
    verify_password,
)


@pytest.fixture()
def settings() -> Settings:
    return Settings(jwt_secret="unit-test-secret-32-bytes-long!!", jwt_ttl_minutes=10)


def test_password_hash_roundtrip_and_salt() -> None:
    first = hash_password("S3cret!")
    second = hash_password("S3cret!")
    assert first != second, "必须每次生成新盐"
    assert verify_password("S3cret!", first)
    assert not verify_password("s3cret!", first)
    assert not verify_password("S3cret!", None)
    assert not verify_password("S3cret!", "garbage")


def test_jwt_roundtrip(settings: Settings) -> None:
    token, expires_at = create_admin_token(7, "admin", "admin", settings)
    payload = decode_admin_token(token, settings)
    assert payload["sub"] == "7"
    assert payload["account"] == "admin"
    assert payload["scope"] == "admin"
    assert expires_at > datetime.now(UTC)


def test_jwt_rejects_wrong_secret(settings: Settings) -> None:
    token, _ = create_admin_token(7, "admin", "admin", settings)
    other = Settings(jwt_secret="other-secret-32-bytes-long-for-tests!!")
    with pytest.raises(jwt.PyJWTError):
        decode_admin_token(token, other)


def test_jwt_rejects_expired(settings: Settings) -> None:
    expired = jwt.encode(
        {
            "sub": "7",
            "scope": "admin",
            "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_admin_token(expired, settings)


def test_jwt_rejects_widget_scope(settings: Settings) -> None:
    """双链隔离：scope 不是 admin 的 token 不能进治理域。"""
    widget_token = jwt.encode(
        {
            "sub": "1",
            "scope": "widget",
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_admin_token(widget_token, settings)
