"""
AES-256-GCM string encryption helpers.

Ciphertext layout (all bytes):
  nonce (12 bytes) || AES-GCM ciphertext (includes authentication tag)

The nonce is random per encryption so identical plaintexts yield distinct
ciphertexts. ``key_hex`` must be a 64-character hex string (32-byte key).
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_SIZE = 12


def encrypt_string(plaintext: str, key_hex: str) -> bytes:
    """
    Encrypt a UTF-8 string with AES-256-GCM.

    Args:
        plaintext: Value to encrypt.
        key_hex: 32-byte AES key encoded as 64 hex characters.

    Returns:
        Nonce concatenated with the authenticated ciphertext.
    """
    key = bytes.fromhex(key_hex)
    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt_bytes(ciphertext: bytes, key_hex: str) -> str:
    """
    Decrypt bytes produced by ``encrypt_string``.

    Args:
        ciphertext: Nonce || AES-GCM ciphertext from ``encrypt_string``.
        key_hex: Same 64-character hex key used for encryption.

    Returns:
        Original UTF-8 plaintext string.
    """
    key = bytes.fromhex(key_hex)
    nonce = ciphertext[:_NONCE_SIZE]
    encrypted = ciphertext[_NONCE_SIZE:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, encrypted, None)
    return plaintext.decode("utf-8")
