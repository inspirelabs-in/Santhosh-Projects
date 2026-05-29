"""Unit tests for JWT screening URL generation + verification."""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from src.services.screening_url import (
    InvalidScreeningToken,
    generate_screening_token,
    generate_screening_url,
    verify_screening_token,
)


def test_roundtrip_valid_token():
    app_id, role_id, cand_id = uuid4(), uuid4(), uuid4()
    token, exp = generate_screening_token(
        application_id=app_id, role_id=role_id, candidate_id=cand_id
    )
    claims = verify_screening_token(token)
    assert claims.application_id == app_id
    assert claims.role_id == role_id
    assert claims.candidate_id == cand_id
    assert claims.expires_at == exp


def test_expired_token_rejected():
    token, _ = generate_screening_token(
        application_id=uuid4(), role_id=uuid4(), candidate_id=uuid4(), ttl_days=0
    )
    # ttl_days=0 → exp in the past (roughly now). Ensure we're definitely past it.
    time.sleep(1.0)
    with pytest.raises(InvalidScreeningToken):
        verify_screening_token(token)


def test_tampered_token_rejected():
    token, _ = generate_screening_token(
        application_id=uuid4(), role_id=uuid4(), candidate_id=uuid4()
    )
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(InvalidScreeningToken):
        verify_screening_token(tampered)


def test_generate_screening_url_format():
    url, _ = generate_screening_url(
        application_id=uuid4(), role_id=uuid4(), candidate_id=uuid4()
    )
    assert "/screen/" in url
    token = url.rsplit("/", 1)[-1]
    assert len(token) > 20
