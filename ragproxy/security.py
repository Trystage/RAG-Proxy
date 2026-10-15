"""安全模块：面板 JWT 登录、下游 API 可选 key 校验、常量时间比较。"""
from __future__ import annotations

import datetime
import hmac
import time
from collections.abc import Mapping
from typing import Any


KeyConfig = str | Mapping[str, Any]

import jwt

ALGORITHM = "HS256"
ISSUER = "rag-proxy"


def create_token(secret_key: str, ttl_hours: int = 24) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": "admin",
        "iss": ISSUER,
        "iat": now,
        "exp": now + datetime.timedelta(hours=ttl_hours),
    }
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def verify_token(secret_key: str, token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(
            token, secret_key, algorithms=[ALGORITHM], issuer=ISSUER
        )
        return payload
    except jwt.PyJWTError:
        return None


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def check_secret(secret_key: str, candidate: str) -> bool:
    if not secret_key or not candidate:
        return False
    return constant_time_eq(secret_key, candidate)


def _key_is_valid(key_config: KeyConfig, candidate: str, model: str | None) -> bool:
    if isinstance(key_config, str):
        return constant_time_eq(key_config, candidate)
    if not key_config.get("enabled", True):
        return False

    key_value = key_config.get("key")
    if not isinstance(key_value, str) or not constant_time_eq(key_value, candidate):
        return False

    expires_at = key_config.get("expires_at")
    if expires_at:
        try:
            if isinstance(expires_at, (int, float)):
                expired = time.time() >= expires_at
            else:
                expiry_text = str(expires_at).replace("Z", "+00:00")
                expiry = datetime.datetime.fromisoformat(expiry_text)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=datetime.timezone.utc)
                expired = datetime.datetime.now(datetime.timezone.utc) >= expiry
            if expired:
                return False
        except (TypeError, ValueError, OverflowError):
            return False

    allowed_models = key_config.get("allowed_models") or []
    if allowed_models and (not model or model not in allowed_models):
        return False
    return True


def check_api_key(
    allowed_keys: list[KeyConfig],
    candidate: str | None,
    model: str | None = None,
) -> bool:
    if not allowed_keys:
        return True  # 未配置 key 校验，放行
    if not candidate:
        return False
    return any(_key_is_valid(key_config, candidate, model) for key_config in allowed_keys)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def now_ts() -> int:
    return int(time.time())
