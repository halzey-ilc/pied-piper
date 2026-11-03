"""Cabinet adapters: the roster from the control plane and the day aggregates from
the tenant's meetings, activities, payments and absences (read-only)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

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
from django.db.models.functions import TruncDate

from modules.access_control.domain.rbac import Role, product_role
from modules.activities.infrastructure.models import ActivityRecord
from modules.meetings.infrastructure.models import MeetingRecord
from modules.organizations.infrastructure.models import Membership
from modules.payments.infrastructure.models import PaymentRecord
from modules.payments.infrastructure.sales import deal_sales
from modules.planning.infrastructure.models import AbsenceRecord, PlanRecord
from modules.sales_pipeline.infrastructure.models import DealRecord, DealStageChangeRecord
from modules.team_cabinet.domain.board import Member, RawDay
from modules.team_cabinet.domain.period import Discipline, PeriodMetrics


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
    def __init__(self, using: str, currency: str = "KGS") -> None:
        self.using = using
        # Money sums use this currency only; other currencies are not added to them.
        self.currency = currency

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
        self._add_meetings(
            result, user_ids, day_start, day_end, outcome_cutoff, morning_cutoff, now
        )
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
                today_n=Count("id", filter=Q(due_date=today)),
            )
        )
        for row in rows:
            raw = out.get(row["assignee_user_id"])
            if raw is not None:
                raw.open_tasks = row["open_n"]
                raw.overdue_tasks = row["overdue_n"]
                raw.tasks_due_today = row["today_n"]
        done = (
            ActivityRecord.objects.using(self.using)
            .filter(
                kind="task",
                task_status="completed",
                assignee_user_id__in=user_ids,
                completed_at__date=today,
            )
            .values("assignee_user_id")
            .annotate(n=Count("id"))
        )
        for row in done:
            raw = out.get(row["assignee_user_id"])
            if raw is not None:
                raw.tasks_done_today = row["n"]

    def _add_meetings(
        self,
        out: dict[UUID, RawDay],
        user_ids: list[UUID],
        day_start: datetime,
        day_end: datetime,
        outcome_cutoff: datetime,
        morning_cutoff: datetime,
        now: datetime,
    ) -> None:
        upcoming_unconfirmed = Q(
            status="scheduled", confirmed_at__isnull=True, scheduled_start__gte=now
        )
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
                cancelled=Count("id", filter=Q(status="cancelled")),
                to_confirm=Count("id", filter=upcoming_unconfirmed),
                next_to_confirm=Min("scheduled_start", filter=upcoming_unconfirmed),
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
            raw.meetings_cancelled = row["cancelled"]
            raw.meetings_to_confirm = row["to_confirm"]
            raw.next_to_confirm_at = row["next_to_confirm"]

    def _add_payments(self, out: dict[UUID, RawDay], user_ids: list[UUID], today: date) -> None:
        rows = (
            PaymentRecord.counted.using(self.using)
            .filter(seller_user_id__in=user_ids, paid_on=today, is_refund=False)
            .values("seller_user_id")
            .annotate(n=Count("id"), total=Sum("amount", filter=Q(currency=self.currency)))
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
            PaymentRecord.counted.using(self.using)
            .filter(
                seller_user_id__in=user_ids,
                paid_on__gte=month_start,
                paid_on__lte=month_end,
                currency=self.currency,
            )
            .values("seller_user_id")
            .annotate(total=Sum(signed))
        )
        return {row["seller_user_id"]: row["total"] or Decimal(0) for row in rows}

    def monthly_sales_counts(
        self, user_ids: list[UUID], month_start: date, month_end: date
    ) -> dict[UUID, int]:
        """Sales per seller: deals whose first payment fell in the month (payments.sales)."""
        if not user_ids:
            return {}
        counts: dict[UUID, int] = {}
        for sale in deal_sales(self.using, month_start, month_end, sellers=user_ids):
            counts[sale.seller_user_id] = counts.get(sale.seller_user_id, 0) + 1
        return counts

    def department_sales_target(self, department_id: UUID, month: str) -> Decimal | None:
        """The department's monthly_sales plan target (department-scope plan), if set."""
        return (
            PlanRecord.objects.using(self.using)
            .filter(
                scope="department", scope_id=department_id, metric="monthly_sales", period=month
            )
            .values_list("target", flat=True)
            .first()
        )

    def period_metrics(
        self, user_ids: list[UUID], start: date, end: date, *, tz_name: str
    ) -> dict[UUID, PeriodMetrics]:
        """Per user over company-local days [start, end]: meetings, payments, sales, calls,
        stage changes, done tasks and the per-day activity used for days without activity."""
        out: dict[UUID, PeriodMetrics] = {uid: PeriodMetrics() for uid in user_ids}
        if not user_ids:
            return out
        tz = ZoneInfo(tz_name)
        begin = datetime(start.year, start.month, start.day, tzinfo=tz)
        finish = datetime(end.year, end.month, end.day, tzinfo=tz) + timedelta(days=1)

        def bump(uid: UUID | None, day: date | None, field: str, n: int) -> None:
            if uid is None or uid not in out or day is None:
                return
            out[uid].daily.setdefault(day, {}).setdefault(field, 0)
            out[uid].daily[day][field] += n

        meetings = MeetingRecord.objects.using(self.using).filter(
            owner_user_id__in=user_ids, scheduled_start__gte=begin, scheduled_start__lt=finish
        )
        for row in meetings.values("owner_user_id").annotate(
            booked=Count("id", filter=~Q(status="cancelled")),
            held=Count("id", filter=Q(status="held")),
            no_show=Count("id", filter=Q(status="no_show")),
            confirmed=Count("id", filter=Q(confirmed_at__isnull=False)),
            cancelled=Count("id", filter=Q(status="cancelled")),
        ):
            m = out[row["owner_user_id"]]
            m.meetings_booked = row["booked"]
            m.meetings_held = row["held"]
            m.meetings_no_show = row["no_show"]
            m.meetings_confirmed = row["confirmed"]
            m.meetings_cancelled = row["cancelled"]
        for row in (
            meetings.annotate(day=TruncDate("scheduled_start", tzinfo=tz))
            .values("owner_user_id", "day")
            .annotate(n=Count("id"))
        ):
            bump(row["owner_user_id"], row["day"], "meetings", row["n"])

        calls = ActivityRecord.objects.using(self.using).filter(
            kind="call", owner_user_id__in=user_ids, occurred_at__gte=begin, occurred_at__lt=finish
        )
        for row in (
            calls.annotate(day=TruncDate("occurred_at", tzinfo=tz))
            .values("owner_user_id", "day")
            .annotate(n=Count("id"))
        ):
            out[row["owner_user_id"]].calls += row["n"]
            bump(row["owner_user_id"], row["day"], "calls", row["n"])

        changes = DealStageChangeRecord.objects.using(self.using).filter(
            author_user_id__in=user_ids, created_at__gte=begin, created_at__lt=finish
        )
        for row in (
            changes.annotate(day=TruncDate("created_at", tzinfo=tz))
            .values("author_user_id", "day")
            .annotate(n=Count("id"))
        ):
            out[row["author_user_id"]].stage_changes += row["n"]
            bump(row["author_user_id"], row["day"], "stage_changes", row["n"])

        payments = PaymentRecord.counted.using(self.using).filter(
            seller_user_id__in=user_ids, paid_on__gte=start, paid_on__lte=end, is_refund=False
        )
        for row in payments.values("seller_user_id", "paid_on").annotate(
            n=Count("id"), total=Sum("amount", filter=Q(currency=self.currency))
        ):
            m = out[row["seller_user_id"]]
            m.payments_count += row["n"]
            m.received += row["total"] or Decimal(0)
            bump(row["seller_user_id"], row["paid_on"], "payments", row["n"])
        prepay = payments.filter(kind="prepayment").values("seller_user_id").annotate(n=Count("id"))
        for row in prepay:
            out[row["seller_user_id"]].prepayments = row["n"]

        for sale in deal_sales(self.using, start, end, sellers=user_ids):
            out[sale.seller_user_id].sales += 1

        done = (
            ActivityRecord.objects.using(self.using)
            .filter(
                kind="task",
                task_status="completed",
                assignee_user_id__in=user_ids,
                completed_at__gte=begin,
                completed_at__lt=finish,
            )
            .values("assignee_user_id")
            .annotate(n=Count("id"))
        )
        for row in done:
            out[row["assignee_user_id"]].tasks_done = row["n"]
        return out

    def discipline(
        self, user_ids: list[UUID], *, now: datetime, today: date
    ) -> dict[UUID, Discipline]:
        """Current CRM tails per user (a snapshot, not a period figure)."""
        out: dict[UUID, Discipline] = {uid: Discipline() for uid in user_ids}
        if not user_ids:
            return out
        for row in (
            MeetingRecord.objects.using(self.using)
            .filter(owner_user_id__in=user_ids)
            .values("owner_user_id")
            .annotate(
                no_outcome=Count("id", filter=Q(status="scheduled", scheduled_end__lt=now)),
                no_date=Count("id", filter=Q(status="rescheduled")),
            )
        ):
            out[row["owner_user_id"]].meetings_without_outcome = row["no_outcome"]
            out[row["owner_user_id"]].reschedules_without_date = row["no_date"]
        for row in (
            DealRecord.objects.using(self.using)
            .filter(owner_user_id__in=user_ids, status="open")
            .filter(Q(next_step__isnull=True) | Q(next_step=""))
            .values("owner_user_id")
            .annotate(n=Count("id"))
        ):
            out[row["owner_user_id"]].deals_without_next_step = row["n"]
        for row in (
            ActivityRecord.objects.using(self.using)
            .filter(
                kind="task", task_status="open", assignee_user_id__in=user_ids, due_date__lt=today
            )
            .values("assignee_user_id")
            .annotate(n=Count("id"))
        ):
            out[row["assignee_user_id"]].overdue_tasks = row["n"]
        return out
