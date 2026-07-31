"""StaffDeck 后端模块：认证基础设施，提供密码哈希、访问令牌签发解析和当前用户依赖。

主要入口：hash_password, verify_password, create_access_token, get_current_user, ensure_current_user_tenant, require_current_tenant；主要协作模块：app.config、app.db、app.db.models。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.config import get_settings
from app.db import get_session
from app.db.models import User


TOKEN_TTL_SECONDS = 60 * 60 * 24 * 14
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode('utf-8')}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        _algo, salt, _digest = stored_hash.split("$", 2)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    candidate = f"pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode('utf-8')}"
    return hmac.compare_digest(candidate, stored_hash)


def create_access_token(user: User) -> str:
    payload = {
        "tenant_id": user.tenant_id,
        "user_id": user.id,
        "username": user.username,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    body = _b64(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signature = _sign(body)
    return f"{body}.{signature}"


# 阅读提示：FastAPI 鉴权依赖，从 Bearer 令牌恢复用户，后续仍需做租户和资源权限检查。
def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_session),
) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = _decode_token(credentials.credentials)
    user = db.get(User, payload.get("user_id", ""))
    if not user or user.tenant_id != payload.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Invalid user token")
    return user


def ensure_current_user_tenant(tenant_id: str, current_user: User) -> None:
    if not isinstance(current_user, User):
        raise HTTPException(status_code=401, detail="Not authenticated")
    if tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")


def require_current_tenant(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
) -> User:
    ensure_current_user_tenant(tenant_id, current_user)
    return current_user


def _decode_token(token: str) -> dict[str, Any]:
    try:
        body, signature = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    if not hmac.compare_digest(_sign(body), signature):
        raise HTTPException(status_code=401, detail="Invalid token signature")
    try:
        payload = json.loads(base64.urlsafe_b64decode(_pad_b64(body)).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="Token expired")
    return payload


def _sign(body: str) -> str:
    secret = get_settings().app_secret.encode("utf-8")
    return _b64(hmac.new(secret, body.encode("utf-8"), hashlib.sha256).digest())


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _pad_b64(value: str) -> bytes:
    return (value + "=" * (-len(value) % 4)).encode("utf-8")
