"""StaffDeck 后端模块：渠道凭据加解密边界，基于应用密钥保护数据库中的渠道秘密。

主要入口：encrypt_channel_secret, decrypt_channel_secret；主要协作模块：app.config。阅读时先从这些入口跟踪调用关系。
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet

from app.config import get_settings

logger = logging.getLogger(__name__)

_warned_fallback_key = False


def _fernet_key() -> bytes:
    """渠道凭证加密密钥：CHANNEL_SECRET 缺省时由 APP_SECRET 派生（仅开发兜底）。"""
    global _warned_fallback_key
    settings = get_settings()
    secret = settings.channel_secret
    if not secret:
        if not _warned_fallback_key:
            _warned_fallback_key = True
            logger.warning("CHANNEL_SECRET 未配置，渠道凭证改用 APP_SECRET 派生密钥加密，生产环境请务必配置")
        secret = f"{settings.app_secret}:channel"
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_channel_secret(plaintext: str) -> str:
    return Fernet(_fernet_key()).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_channel_secret(token: str) -> str:
    return Fernet(_fernet_key()).decrypt(token.encode("utf-8")).decode("utf-8")
