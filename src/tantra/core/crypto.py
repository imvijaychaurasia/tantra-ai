"""
Tantra AI — Token encryption utility
Fernet symmetric encryption for OAuth tokens at rest.

Key derivation:
  SHA-256(app.secret_key) → 32 bytes → URL-safe base64 → Fernet key

Usage:
    from tantra.core.crypto import encrypt_token, decrypt_token

    enc = encrypt_token("my-access-token")
    raw = decrypt_token(enc)
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """
    Build a Fernet cipher keyed from the app secret_key.
    Cached so the key derivation only runs once per process.
    """
    from tantra.core.config import settings  # lazy import — avoids circular

    raw = settings.secret_key.get_secret_value().encode("utf-8")
    # SHA-256 gives us exactly 32 bytes; Fernet wants URL-safe base64 of 32 bytes
    key_bytes = hashlib.sha256(raw).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def encrypt_token(token: str) -> str:
    """
    Encrypt a plain-text token string.
    Returns a URL-safe base64 Fernet token (safe to store in TEXT column).
    """
    if not token:
        raise ValueError("Cannot encrypt an empty token")
    return _get_fernet().encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted: str) -> str:
    """
    Decrypt a previously encrypted token.
    Raises cryptography.fernet.InvalidToken if tampering is detected.
    """
    if not encrypted:
        raise ValueError("Cannot decrypt an empty string")
    try:
        return _get_fernet().decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Token decryption failed — possible key mismatch or corruption") from exc
