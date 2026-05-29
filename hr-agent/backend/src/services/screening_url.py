"""JWT-signed screening form URLs.

Tokens carry (application_id, role_id, candidate_id) + expiry. Signed with
HS256 using `Settings.jwt_signing_secret`. We keep the payload small so the
URL stays short enough for WhatsApp body text and SMS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from jose import JWTError, jwt

from src.config import get_settings

_settings = get_settings()
_ALGORITHM = "HS256"


class InvalidScreeningToken(Exception):
    """Token is expired, tampered with, or malformed."""


@dataclass
class ScreeningTokenClaims:
    application_id: UUID
    role_id: UUID
    candidate_id: UUID
    expires_at: datetime
    action: str = "screening"  # "screening" | "assignment" (V1)


@dataclass
class ApplyTokenClaims:
    """V1 action-scoped token (thinner: only application_id + action + exp).

    Used by self-hosted /apply/[token] flow where role + candidate can be
    resolved from application_id on the server.
    """

    application_id: UUID
    action: str  # "screening" | "assignment"
    expires_at: datetime


def generate_screening_token(
    *,
    application_id: UUID,
    role_id: UUID,
    candidate_id: UUID,
    ttl_days: int | None = None,
) -> tuple[str, datetime]:
    ttl = ttl_days if ttl_days is not None else _settings.screening_url_ttl_days
    # JWT ``exp`` is integer-seconds; truncate microseconds on the returned
    # ``exp`` so callers comparing the returned value to a verified token's
    # claims.expires_at don't trip over a sub-second mismatch.
    exp = (datetime.now(tz=UTC) + timedelta(days=ttl)).replace(microsecond=0)
    payload = {
        "app_id": str(application_id),
        "role_id": str(role_id),
        "cand_id": str(candidate_id),
        "iss": _settings.jwt_issuer,
        "exp": int(exp.timestamp()),
        "iat": int(datetime.now(tz=UTC).timestamp()),
    }
    token = jwt.encode(payload, _settings.jwt_signing_secret, algorithm=_ALGORITHM)
    return token, exp


def generate_screening_url(
    *,
    application_id: UUID,
    role_id: UUID,
    candidate_id: UUID,
    ttl_days: int | None = None,
) -> tuple[str, datetime]:
    token, exp = generate_screening_token(
        application_id=application_id,
        role_id=role_id,
        candidate_id=candidate_id,
        ttl_days=ttl_days,
    )
    return f"{_settings.app_base_url.rstrip('/')}/screen/{token}", exp


def verify_screening_token(token: str) -> ScreeningTokenClaims:
    try:
        payload = jwt.decode(
            token,
            _settings.jwt_signing_secret,
            algorithms=[_ALGORITHM],
            issuer=_settings.jwt_issuer,
        )
    except JWTError as e:
        raise InvalidScreeningToken(str(e)) from e

    try:
        return ScreeningTokenClaims(
            application_id=UUID(payload["app_id"]),
            role_id=UUID(payload["role_id"]),
            candidate_id=UUID(payload["cand_id"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError) as e:
        raise InvalidScreeningToken(f"malformed claims: {e}") from e


def generate_apply_token(
    *,
    application_id: UUID,
    action: str,
    ttl_days: int | None = None,
) -> str:
    """V1 action-scoped token for /apply/[token]."""
    if action not in ("screening", "assignment", "chat"):
        raise ValueError(f"invalid action: {action}")
    ttl = ttl_days if ttl_days is not None else _settings.screening_url_ttl_days
    exp = datetime.now(tz=UTC) + timedelta(days=ttl)
    payload = {
        "app_id": str(application_id),
        "act": action,
        "iss": _settings.jwt_issuer,
        "exp": int(exp.timestamp()),
        "iat": int(datetime.now(tz=UTC).timestamp()),
    }
    return jwt.encode(payload, _settings.jwt_signing_secret, algorithm=_ALGORITHM)


def verify_apply_token(token: str) -> ApplyTokenClaims:
    try:
        payload = jwt.decode(
            token,
            _settings.jwt_signing_secret,
            algorithms=[_ALGORITHM],
            issuer=_settings.jwt_issuer,
        )
    except JWTError as e:
        raise InvalidScreeningToken(str(e)) from e
    try:
        return ApplyTokenClaims(
            application_id=UUID(payload["app_id"]),
            action=payload["act"],
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError) as e:
        raise InvalidScreeningToken(f"malformed claims: {e}") from e
