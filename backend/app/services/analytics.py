"""Analytics and dashboard insights (Phase 2I).

Read-only queries over leads, campaigns, outreach messages, replies and
follow-ups. No service method here mutates any row, enqueues, approves or
sends: this milestone is strictly observational and safe to expose.

All "sent" buckets count SENT + DELIVERED; a message that was replied to
terminates in REPLIED and is counted in "replied" (not in "sent"),
matching the state machine semantics used by the queue and follow-ups.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import MessageStatus, OutreachStatus
from app.models.campaign import Campaign
from app.models.lead import Lead
from app.models.outreach_message import OutreachMessage
from app.models.reply import Reply

_SENT_STATUSES = [MessageStatus.SENT.value, MessageStatus.DELIVERED.value]

_ALL_MESSAGE_STATUSES = [s.value for s in MessageStatus]


@dataclass
class LeadFunnel:
    total: int
    by_outreach_status: dict[str, int]
    do_not_contact: int
    by_priority: dict[str, int]


@dataclass
class MessageFunnel:
    total: int
    by_status: dict[str, int]
    sent: int
    replied: int
    failed: int


@dataclass
class CampaignPerformance:
    id: int
    name: str
    template_type: str
    active: bool
    total_messages: int
    drafts: int
    sent: int
    replied: int
    failed: int
    follow_ups: int
    reply_rate: float


@dataclass
class ReplyStats:
    total: int
    by_classification: dict[str, int]
    avg_confidence: float


@dataclass
class FollowUpStats:
    total: int
    created: int
    sent: int
    replied: int
    by_status: dict[str, int]


class AnalyticsService:
    """Pure read-only analytics queries."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Leads
    # ------------------------------------------------------------------

    def lead_funnel(self) -> dict:
        rows = self.db.execute(
            select(Lead.outreach_status, func.count(Lead.id)).group_by(
                Lead.outreach_status
            )
        ).all()
        by_status = {status: count for status, count in rows}

        priority_rows = self.db.execute(
            select(Lead.lead_priority, func.count(Lead.id)).group_by(
                Lead.lead_priority
            )
        ).all()
        by_priority = {priority: count for priority, count in priority_rows}

        total = int(
            self.db.execute(select(func.count(Lead.id))).scalar_one()
        )
        dnc = int(
            self.db.execute(
                select(func.count(Lead.id)).where(Lead.do_not_contact.is_(True))
            ).scalar_one()
        )
        return asdict(
            LeadFunnel(
                total=total,
                by_outreach_status=by_status,
                do_not_contact=dnc,
                by_priority=by_priority,
            )
        )

    def leads_by_city(self, limit: int = 5) -> list[dict]:
        rows = self.db.execute(
            select(Lead.city, func.count(Lead.id))
            .where(Lead.city.isnot(None))
            .group_by(Lead.city)
            .order_by(func.count(Lead.id).desc())
            .limit(limit)
        ).all()
        return [{"city": city, "count": count} for city, count in rows]

    def leads_by_niche(self, limit: int = 5) -> list[dict]:
        rows = self.db.execute(
            select(Lead.niche, func.count(Lead.id))
            .where(Lead.niche.isnot(None))
            .group_by(Lead.niche)
            .order_by(func.count(Lead.id).desc())
            .limit(limit)
        ).all()
        return [{"niche": niche, "count": count} for niche, count in rows]

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def message_funnel(self) -> dict:
        rows = self.db.execute(
            select(OutreachMessage.status, func.count(OutreachMessage.id)).group_by(
                OutreachMessage.status
            )
        ).all()
        by_status: dict[str, int] = {status: 0 for status in _ALL_MESSAGE_STATUSES}
        for status, count in rows:
            by_status[status] = count
        total = int(
            self.db.execute(select(func.count(OutreachMessage.id))).scalar_one()
        )
        sent = int(
            self.db.execute(
                select(func.count(OutreachMessage.id)).where(
                    OutreachMessage.status.in_(_SENT_STATUSES)
                )
            ).scalar_one()
        )
        replied = int(
            self.db.execute(
                select(func.count(OutreachMessage.id)).where(
                    OutreachMessage.status == MessageStatus.REPLIED.value
                )
            ).scalar_one()
        )
        failed = int(
            self.db.execute(
                select(func.count(OutreachMessage.id)).where(
                    OutreachMessage.status == MessageStatus.FAILED.value
                )
            ).scalar_one()
        )
        return asdict(
            MessageFunnel(
                total=total,
                by_status=by_status,
                sent=sent,
                replied=replied,
                failed=failed,
            )
        )

    def messages_sent_today(self) -> int:
        from app.services.queue import OutreachQueue

        return OutreachQueue(self.db).sent_today()

    # ------------------------------------------------------------------
    # Campaigns
    # ------------------------------------------------------------------

    def campaign_performance(self) -> list[dict]:
        campaigns = self.db.execute(
            select(Campaign).order_by(Campaign.id.asc())
        ).scalars().all()
        result: list[dict] = []
        for campaign in campaigns:
            base = select(OutreachMessage.id).where(
                OutreachMessage.campaign_id == campaign.id
            )
            total = self._count(base)
            drafts = self._count(
                base.where(
                    OutreachMessage.status == MessageStatus.DRAFT.value
                )
            )
            sent = self._count(
                base.where(OutreachMessage.status.in_(_SENT_STATUSES))
            )
            replied = self._count(
                base.where(
                    OutreachMessage.status == MessageStatus.REPLIED.value
                )
            )
            failed = self._count(
                base.where(OutreachMessage.status == MessageStatus.FAILED.value)
            )
            follow_ups = self._count(
                base.where(OutreachMessage.template_type == "FOLLOW_UP")
            )
            reply_rate = round(replied / sent, 4) if sent else 0.0
            result.append(
                asdict(
                    CampaignPerformance(
                        id=campaign.id,
                        name=campaign.name,
                        template_type=campaign.template_type,
                        active=campaign.active,
                        total_messages=total,
                        drafts=drafts,
                        sent=sent,
                        replied=replied,
                        failed=failed,
                        follow_ups=follow_ups,
                        reply_rate=reply_rate,
                    )
                )
            )
        return result

    # ------------------------------------------------------------------
    # Replies
    # ------------------------------------------------------------------

    def reply_stats(self) -> dict:
        rows = self.db.execute(
            select(Reply.classification, func.count(Reply.id)).group_by(
                Reply.classification
            )
        ).all()
        by_classification = {classification: count for classification, count in rows}
        total = int(self.db.execute(select(func.count(Reply.id))).scalar_one())
        avg = self.db.execute(
            select(func.avg(Reply.confidence))
        ).scalar_one()
        return asdict(
            ReplyStats(
                total=total,
                by_classification=by_classification,
                avg_confidence=round(float(avg), 4) if avg is not None else 0.0,
            )
        )

    def replies_today(self) -> int:
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(
            self.db.execute(
                select(func.count(Reply.id)).where(Reply.received_at >= midnight)
            ).scalar_one()
        )

    # ------------------------------------------------------------------
    # Follow-ups
    # ------------------------------------------------------------------

    def follow_up_stats(self) -> dict:
        base = select(OutreachMessage.id).where(
            OutreachMessage.template_type == "FOLLOW_UP"
        )
        total = self._count(base)
        created = self._count(
            base.where(OutreachMessage.status == MessageStatus.DRAFT.value)
        )
        sent = self._count(
            base.where(OutreachMessage.status.in_(_SENT_STATUSES))
        )
        replied = self._count(
            base.where(OutreachMessage.status == MessageStatus.REPLIED.value)
        )
        rows = self.db.execute(
            select(OutreachMessage.status, func.count(OutreachMessage.id))
            .where(OutreachMessage.template_type == "FOLLOW_UP")
            .group_by(OutreachMessage.status)
        ).all()
        by_status = {status: count for status, count in rows}
        return asdict(
            FollowUpStats(
                total=total,
                created=created,
                sent=sent,
                replied=replied,
                by_status=by_status,
            )
        )

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    def overview(self) -> dict:
        return {
            "leads": self.lead_funnel(),
            "messages": self.message_funnel(),
            "campaigns": self.campaign_performance(),
            "replies": self.reply_stats(),
            "follow_ups": self.follow_up_stats(),
            "sent_today": self.messages_sent_today(),
            "replies_today": self.replies_today(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _count(self, stmt) -> int:
        return int(self.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())


__all__ = ["AnalyticsService", "OutreachStatus"]