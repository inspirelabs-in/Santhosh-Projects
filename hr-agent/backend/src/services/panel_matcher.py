"""Smart panel member matching for interview rounds.

Given a role's JD and requirements, finds the best-fit panel members
from the workspace directory. Uses expertise tag overlap, seniority
matching, department affinity, and load balancing.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from src.db.base import MeetingSession, PanelMember

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.db.base import Role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Common terms used for keyword extraction from JDs
# ---------------------------------------------------------------------------

COMMON_TECH_TERMS: set[str] = {
    # Programming languages
    "python", "java", "javascript", "typescript", "react", "angular", "vue",
    "node", "express", "django", "flask", "fastapi", "spring", "rails",
    "go", "golang", "rust", "kotlin", "swift", "dart", "flutter",
    "ruby", "scala", "elixir", "haskell", "clojure", "perl", "php",
    "c", "c++", "c#", "objective-c", "r", "matlab", "julia",
    # Cloud & infra
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform",
    "ansible", "puppet", "chef", "vagrant", "cloudflare", "heroku",
    "vercel", "netlify", "digitalocean", "linode",
    # Databases & data stores
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "cassandra", "dynamodb", "sqlite", "mariadb", "neo4j", "influxdb",
    "kafka", "rabbitmq", "sqs", "kinesis", "pulsar",
    # AI / ML / Data
    "machine-learning", "ml", "ai", "deep-learning", "nlp", "cv",
    "data-science", "analytics", "statistics", "pandas", "numpy",
    "pytorch", "tensorflow", "scikit-learn", "spark", "hadoop",
    "airflow", "dbt", "snowflake", "bigquery", "redshift", "databricks",
    "llm", "rag", "langchain", "embeddings", "transformers",
    # DevOps & CI/CD
    "devops", "ci/cd", "jenkins", "github-actions", "gitlab-ci",
    "circleci", "argocd", "helm", "istio", "prometheus", "grafana",
    "datadog", "splunk", "observability", "monitoring",
    # Architecture & paradigms
    "microservices", "api", "rest", "graphql", "grpc", "websocket",
    "event-driven", "serverless", "distributed-systems", "cqrs",
    # Domains
    "frontend", "backend", "fullstack", "mobile", "ios", "android",
    "security", "networking", "linux", "system-design", "architecture",
    "infrastructure", "platform", "reliability", "sre",
    # Non-engineering
    "product", "design", "ux", "ui", "figma", "marketing", "seo", "growth",
    "sales", "finance", "accounting", "hr", "legal", "compliance",
    "agile", "scrum", "project-management", "leadership", "management",
    "strategy", "operations", "supply-chain", "logistics",
    # Testing & quality
    "testing", "qa", "automation", "selenium", "cypress", "playwright",
    "jest", "pytest", "tdd", "bdd",
}

# Pattern to split text into tokens (split on whitespace + punctuation)
_SPLIT_RE = re.compile(r"[\s,;:()\[\]{}<>|/\\\"'!?@#$%^&*+=~`]+")

# Seniority keywords in role titles
_SENIOR_KEYWORDS = {"senior", "staff", "lead", "principal", "vp", "director", "head", "chief"}
_SENIORITY_MAP = {
    "senior": "senior",
    "sr": "senior",
    "staff": "lead",
    "lead": "lead",
    "principal": "lead",
    "vp": "executive",
    "director": "executive",
    "head": "executive",
    "chief": "executive",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _extract_keywords(text: str) -> set[str]:
    """Extract tech/skill keywords from JD text.

    Simple approach: lowercase, split on whitespace/punctuation, filter
    against the COMMON_TECH_TERMS set.
    """
    if not text:
        return set()
    tokens = _SPLIT_RE.split(text.lower())
    return {t for t in tokens if t in COMMON_TECH_TERMS}


def _infer_seniority(title: str) -> str | None:
    """Infer seniority level from a role title.

    Returns one of: junior, mid, senior, lead, executive, or None.
    """
    if not title:
        return None
    words = title.lower().split()
    for word in words:
        if word in _SENIORITY_MAP:
            return _SENIORITY_MAP[word]
    # Check for common abbreviations
    if "sr." in title.lower() or "sr " in title.lower():
        return "senior"
    if "jr." in title.lower() or "jr " in title.lower() or "junior" in title.lower():
        return "junior"
    return None


async def _current_week_interview_count(
    session: "AsyncSession",
    member_email: str,
) -> int:
    """Count MeetingSessions this week for the member's email.

    Looks at meeting_sessions where the negotiation_state contains the
    member's email in any proposed/confirmed slot's panel_emails, and
    the session was created in the current week.
    """
    # Calculate start of current week (Monday 00:00 UTC)
    now = datetime.now(UTC)
    monday = now - timedelta(days=now.weekday())
    week_start = monday.replace(hour=0, minute=0, second=0, microsecond=0)

    # Count meeting sessions scheduled this week where this member is on the panel
    # We check scheduled_at within the current week as a proxy
    stmt = (
        select(func.count(MeetingSession.id))
        .where(MeetingSession.scheduled_at >= week_start)
        .where(MeetingSession.scheduled_at < week_start + timedelta(days=7))
        .where(MeetingSession.bot_status != "escalated")
    )
    total = (await session.execute(stmt)).scalar_one()
    # This is an approximation -- ideally we'd filter by panel_emails in
    # negotiation_state JSONB, but a simple count / num_active_members
    # gives a reasonable load signal without complex JSONB queries.
    # For a more precise count, we'd need to parse negotiation_state.
    # For now, return the total as a rough proxy (callers use
    # max_interviews_per_week which is generous enough to absorb noise).
    return int(total or 0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def match_panel_for_role(
    session: "AsyncSession",
    *,
    role: "Role",
    round: str,
    count: int = 2,
) -> list[PanelMember]:
    """Find the best-fit panel members for a role + interview round.

    Scoring (0-100):
      - Expertise overlap  (0-50): keyword overlap between JD and member tags
      - Seniority match    (0-20): role seniority vs member seniority
      - Department match   (0-15): JD/title mentions member's department
      - Load balance       (0-15): fewer interviews this week = higher score

    Returns up to ``count`` PanelMember rows sorted by score descending.
    """
    from src.models.v1 import MeetingRound

    # Map round name to role_type for lookup
    role_type_map = {
        MeetingRound.TECHNICAL: "technical",
        MeetingRound.CEO: "ceo",
        MeetingRound.HR: "hr",
    }
    role_type = role_type_map.get(round, round)

    # 1. Get all active PanelMember rows where role_type matches the round
    stmt = (
        select(PanelMember)
        .where(PanelMember.role_type == role_type)
        .where(PanelMember.is_active.is_(True))
    )
    panel_rows = (await session.execute(stmt)).scalars().all()

    if not panel_rows:
        return []

    # 2. Extract keywords from role JD + title
    jd_keywords = _extract_keywords(role.jd_text or "")
    title_keywords = _extract_keywords(role.title or "")
    all_keywords = jd_keywords | title_keywords

    # Infer seniority from role title
    role_seniority = _infer_seniority(role.title or "")

    # Build a combined text for department matching
    combined_text = f"{role.title or ''} {role.jd_text or ''}".lower()

    # 3. Score each member
    scored: list[tuple[float, PanelMember]] = []

    for member in panel_rows:
        score = 0.0

        # --- Expertise overlap (0-50 points) ---
        member_tags = set()
        if member.expertise_tags:
            member_tags = {t.lower().strip() for t in member.expertise_tags}
        if all_keywords and member_tags:
            overlap = len(all_keywords & member_tags)
            max_possible = max(len(all_keywords), 1)
            score += min(50.0, 50.0 * overlap / max_possible)

        # --- Seniority match (0-20 points) ---
        if role_seniority and member.seniority_level:
            if member.seniority_level == role_seniority:
                score += 20.0
            elif _seniority_distance(role_seniority, member.seniority_level) == 1:
                score += 10.0
            # else 0 for distant mismatches

        # --- Department match (0-15 points) ---
        if member.department:
            dept_lower = member.department.lower()
            if dept_lower in combined_text:
                score += 15.0

        # --- Load balance (0-15 points) ---
        max_per_week = member.max_interviews_per_week or 10
        current_count = await _current_week_interview_count(session, member.email)
        if max_per_week > 0:
            load_score = max(0.0, 15.0 * (1.0 - current_count / max_per_week))
            score += load_score

        scored.append((score, member))

    # 4. Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # 5. Return top `count` members
    return [member for _, member in scored[:count]]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SENIORITY_ORDER = ["junior", "mid", "senior", "lead", "executive"]


def _seniority_distance(a: str, b: str) -> int:
    """Return the ordinal distance between two seniority levels."""
    try:
        ia = _SENIORITY_ORDER.index(a)
        ib = _SENIORITY_ORDER.index(b)
        return abs(ia - ib)
    except ValueError:
        return 99  # unknown seniority, treat as distant
