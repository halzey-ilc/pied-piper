"""Adapters for the automatic morning/evening reports.

Read-only. Day aggregates reuse the head-of-sales cabinet reader (tasks, meetings, calls,
confirmed payments), new leads reuse the sales KPI counter, absences come from planning;
the few figures nobody computes yet (deals without a next step, stage moves, lost deals,
payments recorded, tasks completed within the company-local day, work sessions) are
queried here.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Count, Q

from modules.access_control.domain.rbac import Role, product_role
from modules.activities.infrastructure.models import ActivityRecord
from modules.notifications.domain.daily_reports import (
    EveningFigures,
    MorningFigures,
    ReportKind,
    ReportSchedule,
    StaffMember,
)
from modules.notifications.infrastructure.models import DailyReportRunRecord
from modules.organizations.infrastructure.models import Membership, Organization
from modules.payments.infrastructure.models import PaymentRecord
from modules.planning.infrastructure.models import WorkSessionRecord
from modules.sales_kpi.application.crm_counts import CountScope, GroupBy
from modules.sales_kpi.infrastructure.crm_counts import DjangoCrmCounter
from modules.sales_pipeline.infrastructure.models import DealRecord, DealStageChangeRecord
from modules.team_cabinet.domain.board import RawDay
from modules.team_cabinet.infrastructure.reader import DjangoCabinetReader

# The cabinet's "morning meetings" cut-off (a required argument; unused by the reports).
_MORNING_CUTOFF_HOUR = 12


def report_schedule(tenant_id: UUID) -> ReportSchedule:
    """The company's report settings (control plane); defaults when the row is missing."""
    row = (
        Organization.objects.using("default")
        .filter(id=tenant_id)
        .values(
            "morning_report_enabled",
            "morning_report_time",
            "evening_report_enabled",
            "evening_report_time",
        )
        .first()
    )
    if row is None:
        return ReportSchedule()
    return ReportSchedule(
        morning_enabled=row["morning_report_enabled"],
        morning_time=row["morning_report_time"],
        evening_enabled=row["evening_report_enabled"],
        evening_time=row["evening_report_time"],
    )


def company_currency(tenant_id: UUID) -> str:
    """The company currency (control plane); report money is summed in it only."""
    currency = (
        Organization.objects.using("default")
        .filter(id=tenant_id)
        .values_list("currency", flat=True)
        .first()
    )
    return currency or "KGS"


class DjangoReportRoster:
    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id

    def staff(self) -> list[StaffMember]:
        rows = (
            Membership.objects.using("default")
            .filter(
                organization_id=self.tenant_id,
                is_active=True,
                organization__is_active=True,
                user__is_active=True,
            )
            .select_related("user", "department")
            .order_by("user_id")
        )
        staff = []
        for row in rows:
            user = row.user
            name = " ".join(p for p in (user.first_name, user.last_name) if p)
            department = row.department
            staff.append(
                StaffMember(
                    user_id=row.user_id,
                    name=name or user.get_username(),
                    role=product_role(Role(row.role)),
                    department_id=row.department_id,
                    department_name=department.name if department is not None else "",
                )
            )
        return staff


def _bounds(day: date, now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=now.tzinfo)
    return start, start + timedelta(days=1)


def _per_user(rows: Iterable[dict[str, Any]], key: str) -> dict[UUID, int]:
    return {row[key]: row["n"] for row in rows}


class DjangoDailyFigures:
    def __init__(self, using: str, currency: str) -> None:
        self.using = using
        self.currency = currency
        self._cabinet = DjangoCabinetReader(using, currency)

    def absent(self, user_ids: list[UUID], day: date) -> set[UUID]:
        return self._cabinet.absent_user_ids(user_ids, day)

    def _raw(self, user_ids: list[UUID], day: date, now: datetime) -> dict[UUID, RawDay]:
        start, end = _bounds(day, now)
        return self._cabinet.day_metrics(
            user_ids,
            day_start=start,
            day_end=end,
            now=now,
            outcome_cutoff=now,
            morning_cutoff=start + timedelta(hours=_MORNING_CUTOFF_HOUR),
            today=day,
        )

    def morning(
        self, user_ids: list[UUID], *, day: date, now: datetime
    ) -> dict[UUID, MorningFigures]:
        if not user_ids:
            return {}
        start, end = _bounds(day, now)
        raw = self._raw(user_ids, day, now)
        no_next_step = _per_user(
            DealRecord.objects.using(self.using)
            .filter(owner_user_id__in=user_ids, status="open")
            .filter(Q(next_step__isnull=True) | Q(next_step=""))
            .values("owner_user_id")
            .annotate(n=Count("id")),
            "owner_user_id",
        )
        sessions = WorkSessionRecord.objects.using(self.using)
        started: set[UUID] | None = None
        if sessions.exists():  # the company uses work sessions
            started = set(
                sessions.filter(
                    user_id__in=user_ids, started_at__gte=start, started_at__lt=end
                ).values_list("user_id", flat=True)
            )
        figures = {}
        for user_id in user_ids:
            day_raw = raw.get(user_id)
            figures[user_id] = MorningFigures(
                tasks_due_today=day_raw.tasks_due_today if day_raw else 0,
                overdue_tasks=day_raw.overdue_tasks if day_raw else 0,
                meetings_today=day_raw.meetings_total if day_raw else 0,
                deals_without_next_step=no_next_step.get(user_id, 0),
                shift_started=None if started is None else user_id in started,
            )
        return figures

    def evening(
        self, user_ids: list[UUID], *, day: date, now: datetime
    ) -> dict[UUID, EveningFigures]:
        if not user_ids:
            return {}
        start, end = _bounds(day, now)
        raw = self._raw(user_ids, day, now)
        in_day = {"created_at__gte": start, "created_at__lt": end}
        # Completed within the company-local day (the cabinet reader uses the UTC date).
        tasks_done = _per_user(
            ActivityRecord.objects.using(self.using)
            .filter(
                kind="task",
                task_status="completed",
                assignee_user_id__in=user_ids,
                completed_at__gte=start,
                completed_at__lt=end,
            )
            .values("assignee_user_id")
            .annotate(n=Count("id")),
            "assignee_user_id",
        )
        moved = _per_user(
            DealStageChangeRecord.objects.using(self.using)
            .filter(author_user_id__in=user_ids, **in_day)
            .values("author_user_id")
            .annotate(n=Count("id")),
            "author_user_id",
        )
        lost = _per_user(
            DealRecord.objects.using(self.using)
            .filter(
                owner_user_id__in=user_ids, status="lost", closed_at__gte=start, closed_at__lt=end
            )
            .values("owner_user_id")
            .annotate(n=Count("id")),
            "owner_user_id",
        )
        # Every payment entered today, whatever its status (money counts confirmed only).
        recorded = _per_user(
            PaymentRecord.objects.using(self.using)
            .filter(seller_user_id__in=user_ids, is_refund=False, **in_day)
            .values("seller_user_id")
            .annotate(n=Count("id")),
            "seller_user_id",
        )
        wanted = set(user_ids)
        leads = {
            row.group_id: row.leads_new
            for row in DjangoCrmCounter(self.using).counts(
                leads=CountScope(),
                customers=CountScope(),
                since=start,
                until=end,
                group_by=GroupBy.USER,
            )
            if row.group_id in wanted
        }
        figures = {}
        for user_id in user_ids:
            day_raw = raw.get(user_id)
            figures[user_id] = EveningFigures(
                calls=day_raw.calls if day_raw else 0,
                meetings_held=day_raw.meetings_held if day_raw else 0,
                meetings_no_show=day_raw.meetings_no_show if day_raw else 0,
                tasks_done=tasks_done.get(user_id, 0),
                deals_moved=moved.get(user_id, 0),
                deals_lost=lost.get(user_id, 0),
                payments_recorded=recorded.get(user_id, 0),
                # Confirmed payments only (PaymentRecord.counted), paid today.
                payments_confirmed=day_raw.payments_count if day_raw else 0,
                confirmed_amount=day_raw.received if day_raw else Decimal(0),
                new_leads=leads.get(user_id, 0),
            )
        return figures


class DjangoReportRuns:
    def __init__(self, using: str) -> None:
        self.using = using

    def done(self, day: date) -> set[ReportKind]:
        kinds = (
            DailyReportRunRecord.objects.using(self.using)
            .filter(local_date=day)
            .values_list("kind", flat=True)
        )
        return {ReportKind(k) for k in kinds if k in set(ReportKind)}

    def mark_done(self, kind: ReportKind, day: date) -> None:
        try:
            with transaction.atomic(using=self.using):
                DailyReportRunRecord.objects.using(self.using).create(
                    kind=kind.value, local_date=day
                )
        except IntegrityError:  # another run marked it first
            return
