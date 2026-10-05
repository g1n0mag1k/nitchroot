"""
AES-256-GCM helpers for encrypting merchant secrets at rest.

Ciphertext layout (deterministic for tests): ``nonce (12 bytes) || encrypted``.
The encrypted segment is AES-GCM ciphertext including the 16-byte auth tag.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH = 12


def _key_from_hex(key_hex: str) -> bytes:
    """Derive a 32-byte AES key from a hex-encoded secret."""
    key = bytes.fromhex(key_hex)
    if len(key) != 32:
        raise ValueError("key_hex must decode to exactly 32 bytes (64 hex characters)")
    return key


def encrypt_string(plaintext: str, key_hex: str) -> bytes:
    """
    Encrypt a UTF-8 string with AES-256-GCM.

    Args:
        plaintext: Value to encrypt.
        key_hex: 64-character hex string (32-byte key).

    Returns:
        ``nonce || ciphertext_with_tag`` bytes suitable for ``decrypt_bytes``.
    """
    key = _key_from_hex(key_hex)
    nonce = os.urandom(_NONCE_LENGTH)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt_bytes(ciphertext: bytes, key_hex: str) -> str:
    """
    Decrypt bytes produced by ``encrypt_string``.

    Args:
        ciphertext: ``nonce || ciphertext_with_tag`` from ``encrypt_string``.
        key_hex: Same 64-character hex key used for encryption.

    Returns:
        Original plaintext string.
    """
    if len(ciphertext) < _NONCE_LENGTH + 16:
        raise ValueError("ciphertext too short")
    key = _key_from_hex(key_hex)
    nonce = ciphertext[:_NONCE_LENGTH]
    encrypted = ciphertext[_NONCE_LENGTH:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, encrypted, None)
    return plaintext.decode("utf-8")
