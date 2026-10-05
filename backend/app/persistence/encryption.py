"""
Authenticated encryption for merchant Stripe restricted keys at rest.

Uses AES-256-GCM with a key derived from application ``secret_key`` via PBKDF2.
Never log or persist plaintext keys.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.core.config import get_settings

_NONCE_LENGTH = 12
_KEY_LENGTH = 32
_PBKDF2_ITERATIONS = 100_000
_PBKDF2_SALT = b"snitchroot.merchant_stripe_key.v1"


class MerchantKeyEncryptionError(Exception):
    """Raised when encryption configuration or crypto operations fail."""


def _derive_aes_key(secret_key: str) -> bytes:
    if len(secret_key) < 32:
        raise MerchantKeyEncryptionError("secret_key is too short for merchant key encryption")
    kdf = PBKDF2HMAC(
        algorithm=SHA256(),
        length=_KEY_LENGTH,
        salt=_PBKDF2_SALT,
        iterations=_PBKDF2_ITERATIONS,
    )
    return kdf.derive(secret_key.encode("utf-8"))


class MerchantKeyEncryption:
    """Encrypt and decrypt merchant Stripe restricted keys."""

    def __init__(self, secret_key: str | None = None) -> None:
        key_material = secret_key if secret_key is not None else get_settings().secret_key
        try:
            self._aes_key = _derive_aes_key(key_material)
        except MerchantKeyEncryptionError:
            raise
        except Exception as exc:
            raise MerchantKeyEncryptionError("invalid encryption key configuration") from exc

    def encrypt(self, plaintext_key: str) -> bytes:
        if not plaintext_key:
            raise MerchantKeyEncryptionError("cannot encrypt empty Stripe key")
        nonce = os.urandom(_NONCE_LENGTH)
        aesgcm = AESGCM(self._aes_key)
        ciphertext = aesgcm.encrypt(nonce, plaintext_key.encode("utf-8"), None)
        return nonce + ciphertext

    def decrypt(self, blob: bytes) -> str:
        if len(blob) < _NONCE_LENGTH + 16:
            raise MerchantKeyEncryptionError("encrypted blob is too short")
        nonce = blob[:_NONCE_LENGTH]
        ciphertext = blob[_NONCE_LENGTH:]
        aesgcm = AESGCM(self._aes_key)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise MerchantKeyEncryptionError("decryption failed") from exc
        return plaintext.decode("utf-8")
