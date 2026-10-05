"""
AES-256-GCM encryption for secrets at rest (e.g. per-merchant Stripe keys).

Key material is derived from ``SECRET_KEY`` via PBKDF2-HMAC-SHA256.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.core.config import get_settings

_PBKDF2_ITERATIONS = 100_000
_SALT = b"snitchroot-stripe-key-v1"
_NONCE_LEN = 12


def _derive_aes_key() -> bytes:
    settings = get_settings()
    kdf = PBKDF2HMAC(
        algorithm=SHA256(),
        length=32,
        salt=_SALT,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(settings.secret_key.encode("utf-8"))


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string; returns URL-safe base64 (nonce || ciphertext)."""
    nonce = os.urandom(_NONCE_LEN)
    aesgcm = AESGCM(_derive_aes_key())
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a value produced by :func:`encrypt`."""
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    nonce, ciphertext = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    aesgcm = AESGCM(_derive_aes_key())
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
