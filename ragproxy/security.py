"""安全模块：面板 JWT 登录、下游 API 可选 key 校验、常量时间比较。"""
from __future__ import annotations

import datetime
import hmac
import time
from typing import Any

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


def check_api_key(allowed_keys: list[str], candidate: str | None) -> bool:
    if not allowed_keys:
        return True  # 未配置 key 校验，放行
    if not candidate:
        return False
    return any(constant_time_eq(k, candidate) for k in allowed_keys)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def now_ts() -> int:
    return int(time.time())
