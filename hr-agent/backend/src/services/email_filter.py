"""Inbound-email funnel — the single, pure decision for what an arriving mail IS.

The mail poller (``services/mail_ingest``) and the Graph webhook
(``api/webhooks_email``) both run every inbound message through
``classify_inbound`` BEFORE doing anything. Like ``pipeline_engine``, this module
is deliberately **DB-free and side-effect-free** so the whole intake gate is
unit-testable (see tests/unit/test_email_filter).

The funnel, in order (first match wins):

  1. REPLY        — the message carries In-Reply-To / References headers. A reply
                    is NEVER a new application; it is thread-routed to the
                    application it belongs to. (Fixes the "reply re-intaken as a
                    fresh candidate" + "reply dropped" bug class, NB-12.)
  2. INTERNAL     — the sender's domain is one of the org's own domains. Internal
                    mail is not an application and is recorded + ignored … UNLESS
                    it looks like a referral (employee forwarding a resume), which
                    is surfaced as an application.
  3. APPLICATION  — external, not a reply, and it clears the deterministic
                    authenticity heuristic (resume attachment and/or a subject
                    that names the job, e.g. "Application for Front End Engineer").
  4. IGNORE       — external but fails the heuristic (newsletter, vendor pitch,
                    spam). Recorded so it is never reprocessed, but not ingested.

The heuristic is 100% deterministic (no LLM): cheap, predictable, and explainable
in the audit trail. The dormant ``CLASSIFY_EMAIL`` LLM gate can later be layered
on top as a borderline tiebreaker without changing this contract.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Words that signal "this mail is about applying for a job".
_APPLICATION_KEYWORDS: frozenset[str] = frozenset(
    {
        "application",
        "applying",
        "apply",
        "applicant",
        "candidature",
        "resume",
        "cv",
        "job",
        "vacancy",
        "opening",
        "position",
        "referral",
        "referring",
    }
)

# Generic job-designation words. Presence of one (plus an application keyword or a
# concrete role-title match) is a strong "this names a job" signal.
_DESIGNATION_WORDS: frozenset[str] = frozenset(
    {
        "engineer",
        "developer",
        "manager",
        "lead",
        "analyst",
        "designer",
        "architect",
        "scientist",
        "consultant",
        "specialist",
        "associate",
        "executive",
        "intern",
        "internship",
        "officer",
        "administrator",
        "coordinator",
        "strategist",
        "marketer",
        "recruiter",
        "accountant",
        "writer",
        "researcher",
    }
)

# Tokens too generic to count as a role-title match on their own.
_TITLE_STOP: frozenset[str] = frozenset(
    {
        "the",
        "for",
        "role",
        "position",
        "job",
        "a",
        "an",
        "of",
        "to",
        "and",
        "senior",
        "junior",
        "lead",
        "i",
        "ii",
        "iii",
    }
)


class InboundKind(StrEnum):
    """What an inbound mail is. Drives the ingest branch."""

    REPLY = "reply"                # thread-route to an existing application
    INTERNAL = "internal"          # org-internal mail; record + ignore
    APPLICATION = "application"    # a genuine job application; ingest + score
    IGNORE = "ignore"              # external non-application; record + ignore


@dataclass(frozen=True)
class InboundDecision:
    """The funnel's verdict. ``reason`` is human-readable for the audit trail;
    ``signals`` exposes the heuristic inputs so a borderline LLM tiebreaker (or a
    human) can be layered on later without re-deriving them."""

    kind: InboundKind
    is_referral: bool = False
    reason: str = ""
    signals: dict[str, object] = field(default_factory=dict)


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return set(_TOKEN_RE.findall(text.lower()))


def domain_of(email: str | None) -> str | None:
    """The lower-cased domain of an email address, or None."""
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower() or None


def is_internal_sender(email: str | None, org_domains: Iterable[str]) -> bool:
    """True when the sender's domain matches (or is a subdomain of) an org domain."""
    dom = domain_of(email)
    if not dom:
        return False
    for raw in org_domains:
        od = (raw or "").strip().lower().lstrip("@")
        if not od:
            continue
        if dom == od or dom.endswith("." + od):
            return True
    return False


def _title_token_match(subject_tokens: set[str], role_titles: Sequence[str]) -> bool:
    """True when the subject contains every significant token of some role title
    (e.g. subject has {front, end, engineer} for title "Front End Engineer")."""
    for title in role_titles:
        title_tokens = [t for t in _TOKEN_RE.findall((title or "").lower())]
        if not title_tokens:
            continue
        significant = [t for t in title_tokens if t not in _TITLE_STOP] or title_tokens
        if all(t in subject_tokens for t in significant):
            return True
    return False


def subject_signals(
    subject: str | None, role_titles: Sequence[str]
) -> tuple[bool, bool, int]:
    """Decompose the subject into authenticity signals.

    Returns ``(has_application_keyword, names_a_role, signal_count)`` where
    ``names_a_role`` is true if the subject matches a concrete open-role title OR
    contains a generic designation word, and ``signal_count`` is how many of the
    two independent signals fired (0..2). Two signals = a subject like
    "Application for Front End Engineer".
    """
    toks = _tokens(subject)
    has_keyword = bool(toks & _APPLICATION_KEYWORDS)
    names_role = bool(toks & _DESIGNATION_WORDS) or _title_token_match(toks, role_titles)
    return has_keyword, names_role, int(has_keyword) + int(names_role)


def classify_inbound(
    *,
    subject: str | None,
    from_email: str | None,
    has_reply_headers: bool,
    has_resume_attachment: bool,
    org_domains: Iterable[str] = (),
    open_role_titles: Sequence[str] = (),
) -> InboundDecision:
    """Decide what an inbound mail is. Pure: no IO, no LLM.

    Authenticity rule for a *new* external application (deterministic):
      - a resume attachment plus at least one subject signal, OR
      - a strong subject alone (both signals: an application keyword AND a role),
        covering body-only applications with no attachment.
    A referral is an internal sender that otherwise looks like an application.
    """
    org_domains = list(org_domains or ())
    role_titles = list(open_role_titles or ())
    has_keyword, names_role, signal_count = subject_signals(subject, role_titles)
    signals: dict[str, object] = {
        "has_reply_headers": has_reply_headers,
        "has_resume_attachment": has_resume_attachment,
        "subject_application_keyword": has_keyword,
        "subject_names_role": names_role,
        "subject_signal_count": signal_count,
        "sender_domain": domain_of(from_email),
    }

    # 1. Replies are never new applications — thread-route them.
    if has_reply_headers:
        return InboundDecision(
            InboundKind.REPLY, reason="carries reply headers (In-Reply-To/References)",
            signals=signals,
        )

    # An inbound looks like an application when the deterministic heuristic clears.
    looks_like_application = (has_resume_attachment and signal_count >= 1) or (
        signal_count >= 2
    )
    signals["looks_like_application"] = looks_like_application

    # 2. Internal sender: org-domain mail. Not an application unless it is a
    #    referral (internal person forwarding a resume that names a role).
    if is_internal_sender(from_email, org_domains):
        if has_resume_attachment and signal_count >= 1:
            return InboundDecision(
                InboundKind.APPLICATION, is_referral=True,
                reason="internal sender forwarding a resume — treated as referral",
                signals=signals,
            )
        return InboundDecision(
            InboundKind.INTERNAL, reason="sender domain is an org domain",
            signals=signals,
        )

    # 3 / 4. External: application if the heuristic clears, else ignore.
    if looks_like_application:
        why = (
            "resume attachment + subject signal"
            if has_resume_attachment
            else "strong subject (application keyword + role)"
        )
        return InboundDecision(
            InboundKind.APPLICATION, reason=f"external application: {why}",
            signals=signals,
        )

    return InboundDecision(
        InboundKind.IGNORE,
        reason="external mail fails the application heuristic (no resume + weak subject)",
        signals=signals,
    )
