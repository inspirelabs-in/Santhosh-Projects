"""Multi-channel selection strategy.

Decides which communication channel to use for a candidate based on:
- Whether they have the channel available (email required, phone/WhatsApp optional)
- Historical response patterns (which channel they respond fastest on)
- Message urgency (high urgency → WhatsApp/SMS, formal → email)
- Time of day (business hours → email, off-hours → WhatsApp)

Returns a ranked list of channels so the supervisor can fall back
if the primary channel fails.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import AuditLog, Candidate

logger = logging.getLogger(__name__)


@dataclass
class ChannelRecommendation:
    channel: str  # "email" | "whatsapp" | "sms" | "chat"
    reason: str
    confidence: float


@dataclass
class ChannelStrategy:
    primary: ChannelRecommendation
    fallbacks: list[ChannelRecommendation]
    available_channels: list[str]


async def select_channel(
    session: AsyncSession,
    candidate_id: UUID,
    *,
    urgency: str = "normal",
    message_type: str = "notification",  # notification | formal | reminder | urgent
) -> ChannelStrategy:
    """Select optimal communication channel for a candidate."""
    candidate = await session.get(Candidate, candidate_id)
    if not candidate:
        return ChannelStrategy(
            primary=ChannelRecommendation("email", "default_no_candidate", 0.5),
            fallbacks=[],
            available_channels=["email"],
        )

    available: list[str] = []
    if candidate.email:
        available.append("email")
    if candidate.phone:
        available.append("whatsapp")
        available.append("sms")

    if not available:
        return ChannelStrategy(
            primary=ChannelRecommendation("email", "no_contact_info", 0.1),
            fallbacks=[],
            available_channels=[],
        )

    # Analyze past response patterns
    audit_rows = (
        await session.execute(
            select(AuditLog)
            .where(AuditLog.candidate_id == candidate_id)
            .where(AuditLog.action.in_([
                "email_sent", "whatsapp_sent", "sms_sent",
                "candidate_replied_email", "candidate_replied_whatsapp",
                "chat_message_received",
            ]))
            .order_by(AuditLog.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    # Count responses per channel
    channel_responses: dict[str, int] = {"email": 0, "whatsapp": 0, "sms": 0}
    for a in audit_rows:
        if "email" in a.action and "replied" in a.action:
            channel_responses["email"] += 1
        elif "whatsapp" in a.action and "replied" in a.action:
            channel_responses["whatsapp"] += 1

    now = datetime.now(UTC)
    hour = now.hour

    recommendations: list[ChannelRecommendation] = []

    # Urgency-based selection
    if urgency == "high":
        if "whatsapp" in available:
            recommendations.append(
                ChannelRecommendation("whatsapp", "high_urgency_instant_channel", 0.9)
            )
        if "email" in available:
            recommendations.append(
                ChannelRecommendation("email", "high_urgency_fallback", 0.7)
            )
    elif message_type == "formal":
        if "email" in available:
            recommendations.append(
                ChannelRecommendation("email", "formal_message_email_preferred", 0.9)
            )
        if "whatsapp" in available:
            recommendations.append(
                ChannelRecommendation("whatsapp", "formal_fallback", 0.4)
            )
    elif message_type == "reminder":
        # Off-hours → WhatsApp (async, less intrusive)
        if 9 <= hour <= 18 and "email" in available:
            recommendations.append(
                ChannelRecommendation("email", "business_hours_reminder", 0.8)
            )
        elif "whatsapp" in available:
            recommendations.append(
                ChannelRecommendation("whatsapp", "off_hours_reminder", 0.8)
            )
        for ch in available:
            if not any(r.channel == ch for r in recommendations):
                recommendations.append(
                    ChannelRecommendation(ch, "available_fallback", 0.5)
                )
    else:
        # Default: prefer channel with most responses, then email
        best_channel = max(
            [ch for ch in available if ch in channel_responses],
            key=lambda ch: channel_responses.get(ch, 0),
            default="email" if "email" in available else available[0],
        )
        recommendations.append(
            ChannelRecommendation(best_channel, "historical_preference", 0.7)
        )
        for ch in available:
            if ch != best_channel:
                recommendations.append(
                    ChannelRecommendation(ch, "available_fallback", 0.5)
                )

    if not recommendations:
        recommendations.append(
            ChannelRecommendation("email", "absolute_default", 0.5)
        )

    return ChannelStrategy(
        primary=recommendations[0],
        fallbacks=recommendations[1:],
        available_channels=available,
    )
