"""Cabinet adapters: the roster from the control plane and the day aggregates from
the tenant's meetings, activities, payments and absences (read-only)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db.models import (
    Case,
    Count,
    DecimalField,
    F,
    Min,
    Q,
    Sum,
    When,
)

from modules.access_control.domain.rbac import Role, product_role
from modules.activities.infrastructure.models import ActivityRecord
from modules.meetings.infrastructure.models import MeetingRecord
from modules.organizations.infrastructure.models import Membership
from modules.payments.infrastructure.models import PaymentRecord
from modules.planning.infrastructure.models import AbsenceRecord, PlanRecord
from modules.team_cabinet.domain.board import Member, RawDay


class DjangoTeamRoster:
    """Active members of a company (optionally one department), from the control plane."""

    def members(self, tenant_id: UUID, department_id: UUID | None) -> list[Member]:
        qs = Membership.objects.filter(organization_id=tenant_id, is_active=True)
        if department_id is not None:
            qs = qs.filter(department_id=department_id)
        rows = list(qs.values("user_id", "role", "department_id"))
        user_ids = [row["user_id"] for row in rows]
        usernames = dict(
            get_user_model().objects.filter(id__in=user_ids).values_list("id", "username")
        )
        return [
            Member(
                user_id=row["user_id"],
                username=usernames.get(row["user_id"], ""),
                department_id=row["department_id"],
                product_role=product_role(Role(row["role"])),
            )
            for row in rows
        ]


class DjangoCabinetReader:
    def __init__(self, using: str) -> None:
        self.using = using

    def day_metrics(
        self,
        user_ids: list[UUID],
        *,
        day_start: datetime,
        day_end: datetime,
        now: datetime,  # part of the port; "today" is bounded by day_start/day_end
        outcome_cutoff: datetime,
        morning_cutoff: datetime,
        today: date,
    ) -> dict[UUID, RawDay]:
        result: dict[UUID, RawDay] = {uid: RawDay() for uid in user_ids}
        if not user_ids:
            return result
        self._add_calls(result, user_ids, day_start, day_end)
        self._add_tasks(result, user_ids, today)
        self._add_meetings(result, user_ids, day_start, day_end, outcome_cutoff, morning_cutoff)
        self._add_payments(result, user_ids, today)
        return result

    def _add_calls(
        self, out: dict[UUID, RawDay], user_ids: list[UUID], day_start: datetime, day_end: datetime
    ) -> None:
        rows = (
            ActivityRecord.objects.using(self.using)
            .filter(
                kind="call",
                owner_user_id__in=user_ids,
                occurred_at__gte=day_start,
                occurred_at__lt=day_end,
            )
            .values("owner_user_id")
            .annotate(n=Count("id"))
        )
        for row in rows:
            out[row["owner_user_id"]].calls = row["n"]

    def _add_tasks(self, out: dict[UUID, RawDay], user_ids: list[UUID], today: date) -> None:
        rows = (
            ActivityRecord.objects.using(self.using)
            .filter(kind="task", task_status="open", assignee_user_id__in=user_ids)
            .values("assignee_user_id")
            .annotate(
                open_n=Count("id"),
                overdue_n=Count("id", filter=Q(due_date__lt=today)),
            )
        )
        for row in rows:
            raw = out.get(row["assignee_user_id"])
            if raw is not None:
                raw.open_tasks = row["open_n"]
                raw.overdue_tasks = row["overdue_n"]

    def _add_meetings(
        self,
        out: dict[UUID, RawDay],
        user_ids: list[UUID],
        day_start: datetime,
        day_end: datetime,
        outcome_cutoff: datetime,
        morning_cutoff: datetime,
    ) -> None:
        rows = (
            MeetingRecord.objects.using(self.using)
            .filter(
                owner_user_id__in=user_ids,
                scheduled_start__gte=day_start,
                scheduled_start__lt=day_end,
            )
            .values("owner_user_id")
            .annotate(
                total=Count("id"),
                confirmed=Count("id", filter=Q(confirmed_at__isnull=False)),
                held=Count("id", filter=Q(status="held")),
                no_show=Count("id", filter=Q(status="no_show")),
                overdue=Count(
                    "id", filter=Q(status="scheduled", scheduled_start__lte=outcome_cutoff)
                ),
                morning=Count("id", filter=Q(scheduled_start__lt=morning_cutoff)),
                morning_confirmed=Count(
                    "id",
                    filter=Q(scheduled_start__lt=morning_cutoff, confirmed_at__isnull=False),
                ),
                rescheduled=Count("id", filter=Q(status="rescheduled")),
            )
        )
        for row in rows:
            raw = out[row["owner_user_id"]]
            raw.meetings_total = row["total"]
            raw.meetings_confirmed = row["confirmed"]
            raw.meetings_held = row["held"]
            raw.meetings_no_show = row["no_show"]
            raw.meetings_overdue_outcome = row["overdue"]
            raw.morning_meetings = row["morning"]
            raw.morning_confirmed = row["morning_confirmed"]
            raw.reschedules_without_date = row["rescheduled"]

    def _add_payments(self, out: dict[UUID, RawDay], user_ids: list[UUID], today: date) -> None:
        rows = (
            PaymentRecord.objects.using(self.using)
            .filter(seller_user_id__in=user_ids, paid_on=today, is_refund=False)
            .values("seller_user_id")
            .annotate(n=Count("id"), total=Sum("amount"))
        )
        for row in rows:
            raw = out.get(row["seller_user_id"])
            if raw is not None:
                raw.payments_count = row["n"]
                raw.received = row["total"] or Decimal(0)

    def absent_user_ids(self, user_ids: list[UUID], day: date) -> set[UUID]:
        if not user_ids:
            return set()
        rows = (
            AbsenceRecord.objects.using(self.using)
            .filter(user_id__in=user_ids, start_date__lte=day, end_date__gte=day)
            .values_list("user_id", flat=True)
        )
        return set(rows)

    def monthly_sales_targets(self, user_ids: list[UUID], month: str) -> dict[UUID, Decimal]:
        if not user_ids:
            return {}
        rows = (
            PlanRecord.objects.using(self.using)
            .filter(
                scope="user",
                metric="monthly_sales",
                period=month,
                scope_id__in=user_ids,
            )
            .values_list("scope_id", "target")
        )
        return {row[0]: row[1] for row in rows if row[0] is not None}

    def monthly_received(
        self, user_ids: list[UUID], month_start: date, month_end: date
    ) -> dict[UUID, Decimal]:
        if not user_ids:
            return {}
        signed = Case(
            When(is_refund=True, then=-F("amount")),
            default=F("amount"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
        rows = (
            PaymentRecord.objects.using(self.using)
            .filter(
                seller_user_id__in=user_ids,
                paid_on__gte=month_start,
                paid_on__lte=month_end,
            )
            .values("seller_user_id")
            .annotate(total=Sum(signed))
        )
        return {row["seller_user_id"]: row["total"] or Decimal(0) for row in rows}

    def monthly_sales_counts(
        self, user_ids: list[UUID], month_start: date, month_end: date
    ) -> dict[UUID, int]:
        if not user_ids:
            return {}
        # Deals whose first non-refund payment fell within the month.
        first_in_month = (
            PaymentRecord.objects.using(self.using)
            .filter(is_refund=False, deal_id__isnull=False)
            .values("deal_id")
            .annotate(first=Min("paid_on"))
            .filter(first__gte=month_start, first__lte=month_end)
        )
        deal_ids = [row["deal_id"] for row in first_in_month]
        counts: dict[UUID, int] = {}
        seen: set[UUID] = set()
        user_set = set(user_ids)
        # The seller of each deal's earliest non-refund payment gets the sale.
        rows = (
            PaymentRecord.objects.using(self.using)
            .filter(deal_id__in=deal_ids, is_refund=False)
            .order_by("deal_id", "paid_on", "created_at")
            .values("deal_id", "seller_user_id")
        )
        for row in rows:
            deal_id = row["deal_id"]
            if deal_id is None or deal_id in seen:
                continue
            seen.add(deal_id)
            seller = row["seller_user_id"]
            if seller in user_set:
                counts[seller] = counts.get(seller, 0) + 1
        return counts
