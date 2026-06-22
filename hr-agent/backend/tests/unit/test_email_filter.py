"""Unit tests for the pure inbound-email funnel (services/email_filter).

DB-free + LLM-free, mirroring tests/unit/test_pipeline_engine. Run with:
    python -m pytest tests/unit/test_email_filter.py
"""

from __future__ import annotations

from src.services.email_filter import (
    InboundKind,
    classify_inbound,
    domain_of,
    is_internal_sender,
    subject_signals,
)

ORG_DOMAINS = ["grabon.in", "inspirelabs.com"]
ROLES = ["Front End Engineer", "Product Owner", "Data Scientist"]


def _classify(**kw):
    base = dict(
        subject=None,
        from_email="someone@example.com",
        has_reply_headers=False,
        has_resume_attachment=False,
        org_domains=ORG_DOMAINS,
        open_role_titles=ROLES,
    )
    base.update(kw)
    return classify_inbound(**base)


# --- helpers ---------------------------------------------------------------


def test_domain_of():
    assert domain_of("A.B@Grabon.IN") == "grabon.in"
    assert domain_of("nope") is None
    assert domain_of(None) is None
    assert domain_of("") is None


def test_is_internal_sender_exact_and_subdomain():
    assert is_internal_sender("hr@grabon.in", ORG_DOMAINS) is True
    assert is_internal_sender("x@careers.grabon.in", ORG_DOMAINS) is True  # subdomain
    assert is_internal_sender("x@gmail.com", ORG_DOMAINS) is False
    assert is_internal_sender(None, ORG_DOMAINS) is False
    assert is_internal_sender("x@grabon.in", []) is False


def test_is_internal_sender_handles_at_prefixed_config():
    assert is_internal_sender("x@grabon.in", ["@grabon.in"]) is True


def test_subject_signals_counts():
    # two signals: keyword + role
    assert subject_signals("Application for Front End Engineer", ROLES) == (True, True, 2)
    # keyword only
    assert subject_signals("My application", ROLES) == (True, False, 1)
    # role only (designation word)
    assert subject_signals("Senior Developer", ROLES) == (False, True, 1)
    # nothing
    assert subject_signals("Hello there", ROLES) == (False, False, 0)
    # exact role-title token match without a designation word
    assert subject_signals("Interested in Product Owner", ROLES) == (False, True, 1)


# --- replies (NB-12) -------------------------------------------------------


def test_reply_headers_always_reply_even_if_looks_like_application():
    d = _classify(
        subject="Application for Front End Engineer",
        has_reply_headers=True,
        has_resume_attachment=True,
    )
    assert d.kind is InboundKind.REPLY


def test_reply_from_internal_is_still_reply():
    d = _classify(from_email="hr@grabon.in", has_reply_headers=True)
    assert d.kind is InboundKind.REPLY


# --- internal --------------------------------------------------------------


def test_internal_plain_mail_ignored():
    d = _classify(from_email="hr@grabon.in", subject="Team lunch")
    assert d.kind is InboundKind.INTERNAL
    assert d.is_referral is False


def test_internal_referral_with_resume_is_application():
    d = _classify(
        from_email="hr@grabon.in",
        subject="Referral: Front End Engineer candidate",
        has_resume_attachment=True,
    )
    assert d.kind is InboundKind.APPLICATION
    assert d.is_referral is True


def test_internal_no_resume_even_with_role_subject_stays_internal():
    # An internal person merely talking about a role (no resume) is not a referral.
    d = _classify(from_email="hr@grabon.in", subject="Front End Engineer headcount")
    assert d.kind is InboundKind.INTERNAL


# --- external applications -------------------------------------------------


def test_external_resume_plus_subject_signal_is_application():
    d = _classify(subject="Application", has_resume_attachment=True)
    assert d.kind is InboundKind.APPLICATION
    assert d.is_referral is False


def test_external_strong_subject_no_attachment_is_application():
    # body-only application: keyword + role in subject (2 signals) clears without attachment
    d = _classify(subject="Applying for Data Scientist", has_resume_attachment=False)
    assert d.kind is InboundKind.APPLICATION


def test_external_resume_but_unrelated_subject_is_ignored():
    # attachment present but subject has zero signals -> not enough
    d = _classify(subject="hi", has_resume_attachment=True)
    assert d.kind is InboundKind.IGNORE


def test_external_role_title_match_with_resume_is_application():
    d = _classify(subject="Product Owner", has_resume_attachment=True)
    assert d.kind is InboundKind.APPLICATION


# --- external ignore (spam / vendor / newsletter) --------------------------


def test_external_weak_subject_no_attachment_ignored():
    d = _classify(subject="Boost your sales 10x", has_resume_attachment=False)
    assert d.kind is InboundKind.IGNORE


def test_external_single_keyword_no_attachment_ignored():
    # only one signal and no resume -> ignore (avoids "Application of AI" spam)
    d = _classify(subject="Application of AI in marketing", has_resume_attachment=False)
    assert d.kind is InboundKind.IGNORE


def test_signals_exposed_for_audit():
    d = _classify(subject="Application for Front End Engineer", has_resume_attachment=True)
    assert d.signals["subject_signal_count"] == 2
    assert d.signals["has_resume_attachment"] is True
    assert d.signals["sender_domain"] == "example.com"
    assert d.signals["looks_like_application"] is True
