"""Symmetric encryption for secret config values stored in `config_settings`.

Uses Fernet (AES-128-CBC + HMAC-SHA256). Key sourced from
`CONFIG_ENCRYPTION_KEY` env (44-char base64). When unset in development we
synthesize a deterministic key from the JWT signing secret so local stacks
work out of the box; production must supply an explicit key.

Plaintext stays in memory only. DB stores `enc::<base64-token>`. Helpers
below are no-ops on already-encrypted values, so `encrypt(encrypt(x)) == encrypt(x)`.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

ENC_PREFIX = "enc::"


def _derive_dev_key() -> str:
    """Fallback for dev: derive a Fernet key from JWT_SIGNING_SECRET.

    Production deployments MUST set CONFIG_ENCRYPTION_KEY explicitly so
    rotating one secret doesn't rotate the other.
    """
    seed = os.environ.get("JWT_SIGNING_SECRET") or "dev-only-fallback-do-not-use-in-prod"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    raw = (os.environ.get("CONFIG_ENCRYPTION_KEY") or "").strip()
    if not raw:
        if (os.environ.get("APP_ENV") or "").lower() == "production":
            raise RuntimeError(
                "CONFIG_ENCRYPTION_KEY is required in production. "
                "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        raw = _derive_dev_key()
        logger.warning(
            "CONFIG_ENCRYPTION_KEY not set — using dev fallback derived from JWT_SIGNING_SECRET. "
            "Do not run this in production."
        )
    return Fernet(raw.encode("ascii"))


def is_encrypted(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt(value: str) -> str:
    """Encrypt plaintext. Idempotent on already-encrypted strings."""
    if value is None:
        return value  # type: ignore[return-value]
    if is_encrypted(value):
        return value
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{ENC_PREFIX}{token}"


def decrypt(value: str) -> str:
    """Decrypt ciphertext. Pass-through on plaintext (back-compat for migration)."""
    if value is None:
        return value  # type: ignore[return-value]
    if not is_encrypted(value):
        return value
    token = value[len(ENC_PREFIX) :]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "config secret decryption failed — CONFIG_ENCRYPTION_KEY may have been rotated"
        ) from exc


def mask(value: str | None) -> str:
    """Return a UI-safe stand-in for a secret. Never echoes plaintext."""
    if not value:
        return ""
    return "***set***"
