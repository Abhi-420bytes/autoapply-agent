"""Encryption at rest (Fernet) and secret masking.

Every credential (LLM API keys, OAuth tokens, portal logins, GitHub token) is stored
through `encrypt`/`decrypt` — normally implicitly via the EncryptedText/EncryptedJSON
column types in app.db.types — so plaintext never reaches the database.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_config


class DecryptionError(Exception):
    """Raised when ciphertext cannot be decrypted with any configured key."""


def _parse_keys(primary: str, previous: str) -> list[Fernet]:
    keys = [primary.strip()] + [k.strip() for k in previous.split(",") if k.strip()]
    try:
        return [Fernet(k.encode()) for k in keys]
    except (ValueError, TypeError) as exc:
        # Deliberately don't include the key material in the message.
        raise ValueError(
            "MASTER_KEY / MASTER_KEY_PREVIOUS must be url-safe base64 32-byte Fernet keys. "
            "Generate one with: python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'"
        ) from exc


@lru_cache
def _cipher() -> MultiFernet:
    cfg = get_config()
    return MultiFernet(
        _parse_keys(cfg.master_key.get_secret_value(), cfg.master_key_previous.get_secret_value())
    )


def reset_cipher_cache() -> None:
    """Drop the cached cipher (used by tests and after key rotation)."""
    _cipher.cache_clear()


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("could not decrypt value with the configured master key(s)") from exc


def rotate(ciphertext: str) -> str:
    """Re-encrypt `ciphertext` under the current primary key."""
    try:
        return _cipher().rotate(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("could not rotate value with the configured master key(s)") from exc


def encrypt_json(value: Any) -> str:
    return encrypt(json.dumps(value, separators=(",", ":")))


def decrypt_json(ciphertext: str) -> Any:
    return json.loads(decrypt(ciphertext))


_PREFIX_RE = re.compile(r"^([A-Za-z]{2,4}[-_])")
_MIN_MASKABLE = 12


def mask_secret(secret: str | None) -> str | None:
    """Return a display-safe version of a secret, e.g. 'sk-...a1b2'.

    Short secrets reveal nothing; long ones reveal at most a short vendor prefix and the
    last 4 characters, which is enough to tell keys apart in the UI.
    """
    if secret is None:
        return None
    if len(secret) < _MIN_MASKABLE:
        return "••••"
    m = _PREFIX_RE.match(secret)
    prefix = m.group(1) if m else ""
    return f"{prefix}...{secret[-4:]}"
