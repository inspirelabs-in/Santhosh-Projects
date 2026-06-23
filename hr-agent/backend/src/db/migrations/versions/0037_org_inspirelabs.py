"""Rebrand org from GrabOn to InspireLabs + seed org context.

Org name → "InspireLabs" (parent company; GrabOn is a flagship brand).
Hiring persona company_name updated to match.
Settings gets an ``org_context`` block with a ~120‑word grounding
description of what InspireLabs does, its scale, and its culture.

Revision ID: 0037_org_inspirelabs
Revises: 0036_application_stage_results
Create Date: 2026-06-23
"""

from __future__ import annotations

import json
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_org_inspirelabs"
down_revision: str | Sequence[str] | None = "0036_application_stage_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERSONA = {
    "company_name": "InspireLabs",
    "mission": (
        "Build a connected growth system where AI agents handle execution "
        "at scale while experienced operators steer strategy — so every "
        "growth cycle compounds instead of resetting."
    ),
    "domain_context": (
        "AI-driven performance marketing. We operate a fleet of AI agents "
        "across seven consumer surfaces (deals, cashback, creator commerce, "
        "search intelligence, B2B discovery) that together influence $4.8B "
        "in GMV annually for 3,800+ brands across e-commerce, travel, "
        "fintech, and consumer tech."
    ),
    "values": [
        {"name": "Ownership", "description": "Take ambiguous problems and ship working solutions. No hand-holding."},
        {"name": "Compounding", "description": "Every cycle feeds the next. Research → execute → learn → compound."},
        {"name": "Builder-First", "description": "Weekly deploys. High autonomy, high accountability. We hire builders, not managers."},
        {"name": "Ship Over Discuss", "description": "AI agents ship the work — content, campaigns, attribution, rewards. Humans steer, not stall."},
        {"name": "Lean & Profitable", "description": "Bootstrapped and profitable since day one. No bloat. Every hire compounds."},
    ],
    "what_good_looks_like": [
        "Takes an ambiguous problem and delivers a working solution within a week",
        "Thinks in systems — connects dots across surfaces instead of optimizing a single metric",
        "Comfortable with AI agents as teammates; writes prompts and reviews outputs, not just code",
        "Makes judgment calls with incomplete data and adjusts fast when wrong",
        "Communicates clearly in writing — async-first, no hand-holding",
    ],
    "anti_patterns": [
        "Waits for detailed specs before starting",
        "Needs repeated context to stay productive",
        "Optimizes for local maxima at the expense of the system",
        "Treats AI as a feature announcement rather than a workflow teammate",
    ],
    "hiring_philosophy": (
        "We hire people who take ambiguous problems and ship working solutions. "
        "No hand-holding. Weekly deploys. High autonomy, high accountability. "
        "Every new hire should raise the bar and compound the team's output."
    ),
    "tone": "Direct, builder-first, no-nonsense. Candidates should feel challenged but respected.",
    "version": 1,
}

_ORG_CONTEXT = {
    "summary": (
        "InspireLabs is an AI-driven performance marketing company, headquartered "
        "in Hyderabad, India. Bootstrapped and profitable since inception, it was "
        "built over 12+ years starting with its flagship platform, GrabOn \u2014 India\u2019s "
        "most trusted destination for verified deals and coupons with 6M+ monthly "
        "shoppers. Today, InspireLabs operates a connected system of AI agents across "
        "seven consumer surfaces: GrabOn (deals and savings), GrabCash (affiliate "
        "earnings), GrabShare (creator commerce), RankDrive (SEO intelligence), and "
        "Alternatives.co (B2B discovery). The platform influences $4.8B in GMV "
        "annually across 3,800+ brand partners spanning e-commerce, travel, fintech, "
        "and consumer tech, reaching 25M+ monthly sessions with 96M+ transactions "
        "per year. The culture is ownership-driven and builder-first \u2014 a lean team "
        "where AI agents handle execution loops while experienced operators steer "
        "strategy, ensuring growth compounds instead of resetting."
    ),
    "headquarters": "Hyderabad, India",
    "founded_year": 2013,
    "platforms": ["GrabOn", "GrabCash", "GrabShare", "RankDrive", "Alternatives.co"],
    "brand_partner_count": 3800,
    "monthly_sessions": "25M+",
    "gmv_influenced": "$4.8B",
}


def upgrade() -> None:
    conn = op.get_bind()
    persona_json = json.dumps(_PERSONA, ensure_ascii=False)
    context_json = json.dumps(_ORG_CONTEXT, ensure_ascii=False)

    conn.execute(
        sa.text(
            "UPDATE organizations "
            "SET name = 'InspireLabs', "
            "    hiring_persona = CAST(:persona AS jsonb), "
            "    settings = settings || CAST(:ctx AS jsonb) "
            "WHERE slug = 'grabon'"
        ),
        {"persona": persona_json, "ctx": f'{{"org_context": {context_json}}}'},
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE organizations "
            "SET name = 'GrabOn', "
            "    hiring_persona = '{}'::jsonb, "
            "    settings = settings - 'org_context' "
            "WHERE slug = 'grabon'"
        )
    )
