"""Read-only queries over other modules' tables and the audit log in the tenant
database. This module never writes them and selects ids, dates and amounts only
(no names or text)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from modules.activities.domain.activity import SubjectType as ActivitySubject
from modules.activities.infrastructure.models import ActivityRecord
from modules.audit.infrastructure.models import AuditRecord
from modules.meetings.infrastructure.models import MeetingRecord
from modules.risk.domain.rules import AuditEvent, OpenDeal, WonDeal
from modules.sales_pipeline.domain.deal import DealStatus
from modules.sales_pipeline.infrastructure.models import DealRecord
from modules.telephony.infrastructure.models import CallRecord


class DjangoRiskReader:
    def __init__(self, using: str) -> None:
        self.using = using

    def won_deals(self, since: datetime) -> list[WonDeal]:
        rows = list(
            DealRecord.objects.using(self.using)
            .filter(status=DealStatus.WON.value, closed_at__gte=since)
            .values("id", "owner_user_id", "closed_at")
        )
        ids = [r["id"] for r in rows]
        deal = ActivitySubject.DEAL.value
        touched: set[UUID | None] = set(
            ActivityRecord.objects.using(self.using)
            .filter(subject_type=deal, subject_id__in=ids)
            .values_list("subject_id", flat=True)
        )
        touched |= set(
            MeetingRecord.objects.using(self.using)
            .filter(subject_type=deal, subject_id__in=ids)
            .values_list("subject_id", flat=True)
        )
        touched |= set(
            CallRecord.objects.using(self.using)
            .filter(subject_type=deal, subject_id__in=ids)
            .values_list("subject_id", flat=True)
        )
        return [
            WonDeal(
                id=r["id"],
                owner_user_id=r["owner_user_id"],
                closed_on=r["closed_at"].date(),
                has_activity=r["id"] in touched,
            )
            for r in rows
            if r["closed_at"] is not None  # filtered on closed_at, so always set
        ]

    def open_deals(self) -> list[OpenDeal]:
        return [
            OpenDeal(id=r["id"], owner_user_id=r["owner_user_id"], amount=r["amount"])
            for r in DealRecord.objects.using(self.using)
            .filter(status=DealStatus.OPEN.value)
            .values("id", "owner_user_id", "amount")
        ]

    def audit_events(self, since: datetime, actions: tuple[str, ...]) -> list[AuditEvent]:
        return [
            AuditEvent(
                action=r["action"],
                actor_user_id=r["actor_user_id"],
                resource_id=r["resource_id"],
                occurred_on=r["occurred_at"].date(),
            )
            for r in AuditRecord.objects.using(self.using)
            .filter(occurred_at__gte=since, action__in=actions)
            .values("action", "actor_user_id", "resource_id", "occurred_at")
        ]
