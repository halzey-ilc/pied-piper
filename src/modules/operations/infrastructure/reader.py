"""Read-only queries over other modules' tables in the tenant database.

Same pattern as telephony's subject checker: this module never writes these
tables, it only selects the columns the rules need (no names, amounts or text).
"""

from __future__ import annotations

from datetime import date, datetime

from django.db.models import Q, QuerySet

from modules.activities.domain.activity import ActivityKind, SubjectType, TaskStatus
from modules.activities.infrastructure.models import ActivityRecord
from modules.leads.domain.lead import LeadStatus
from modules.leads.infrastructure.models import LeadRecord
from modules.operations.application.ports import Visibility
from modules.operations.domain.rules import DealSnapshot, LeadSnapshot, TaskSnapshot
from modules.sales_pipeline.domain.deal import DealStatus
from modules.sales_pipeline.infrastructure.models import DealRecord


def _visible[Row: (LeadRecord, DealRecord, ActivityRecord)](
    qs: QuerySet[Row], visibility: Visibility
) -> QuerySet[Row]:
    if visibility.owner_user_id is None:
        return qs
    condition = Q(owner_user_id=visibility.owner_user_id)
    if visibility.team_ids:
        condition |= Q(team_id__in=visibility.team_ids)
    return qs.filter(condition)


class DjangoOperationsReader:
    def __init__(self, using: str) -> None:
        self.using = using

    def stale_lead_candidates(
        self, visibility: Visibility, updated_before: datetime
    ) -> list[LeadSnapshot]:
        qs = LeadRecord.objects.using(self.using).filter(
            status__in=(LeadStatus.NEW.value, LeadStatus.QUALIFIED.value),
            updated_at__lte=updated_before,
        )
        return [
            LeadSnapshot(id=r["id"], owner_user_id=r["owner_user_id"], updated_at=r["updated_at"])
            for r in _visible(qs, visibility).values("id", "owner_user_id", "updated_at")
        ]

    def open_deals(self, visibility: Visibility, today: date) -> list[DealSnapshot]:
        rows = list(
            _visible(
                DealRecord.objects.using(self.using).filter(status=DealStatus.OPEN.value),
                visibility,
            ).values("id", "owner_user_id", "updated_at")
        )
        # A next step is any open task on the deal that is not overdue, whoever owns it.
        with_next_step = set(
            ActivityRecord.objects.using(self.using)
            .filter(
                kind=ActivityKind.TASK.value,
                task_status=TaskStatus.OPEN.value,
                subject_type=SubjectType.DEAL.value,
                subject_id__in=[r["id"] for r in rows],
            )
            .filter(Q(due_date__isnull=True) | Q(due_date__gte=today))
            .values_list("subject_id", flat=True)
        )
        return [
            DealSnapshot(
                id=r["id"],
                owner_user_id=r["owner_user_id"],
                updated_at=r["updated_at"],
                has_next_step=r["id"] in with_next_step,
            )
            for r in rows
        ]

    def overdue_tasks(self, visibility: Visibility, today: date) -> list[TaskSnapshot]:
        qs = ActivityRecord.objects.using(self.using).filter(
            kind=ActivityKind.TASK.value,
            task_status=TaskStatus.OPEN.value,
            due_date__lt=today,  # excludes tasks without a due date
        )
        return [
            TaskSnapshot(
                id=r["id"],
                owner_user_id=r["owner_user_id"],
                due_date=r["due_date"],
                deal_id=r["subject_id"] if r["subject_type"] == SubjectType.DEAL.value else None,
            )
            for r in _visible(qs, visibility).values(
                "id", "owner_user_id", "due_date", "subject_type", "subject_id"
            )
            if r["due_date"] is not None
        ]
