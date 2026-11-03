"""Read-only SQL for the cabinet analytics: funnel by stage, losses and reasons, the lead
first-response speed and tasks as they stood at the end of a past day."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from statistics import median
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import Count, Min, Q, QuerySet

from modules.activities.infrastructure.models import ActivityRecord
from modules.audit.infrastructure.models import AuditRecord
from modules.leads.infrastructure.models import LeadRecord
from modules.meetings.infrastructure.models import MeetingRecord
from modules.sales_pipeline.infrastructure.models import (
    DealRecord,
    DealStageChangeRecord,
    LossReasonRecord,
    PipelineRecord,
    StageRecord,
)
from modules.team_cabinet.domain.analytics import (
    LeadResponse,
    MeetingReasons,
    ReasonCount,
    StageStat,
)

NO_REASON = "Причина не указана"
# A first response to a lead: any activity on it or a decision about it.
_RESPONSE_ACTIONS = ("lead.qualified", "lead.disqualified", "lead.converted", "lead.updated")


def _months(start: date, end: date) -> list[str]:
    months: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _reasons(rows: list[tuple[str, str]]) -> list[ReasonCount]:
    """(reason, month) pairs → reasons with totals and per-month counts, most frequent first."""
    totals: dict[str, int] = defaultdict(int)
    by_month: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for reason, month in rows:
        totals[reason] += 1
        by_month[reason][month] += 1
    return [
        ReasonCount(reason=reason, total=total, by_month=dict(by_month[reason]))
        for reason, total in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    ]


class DjangoAnalyticsReader:
    def __init__(self, using: str, currency: str) -> None:
        self.using = using
        self.currency = currency

    # --- pipelines --------------------------------------------------------------

    def pipeline_for(
        self, pipeline_id: UUID | None, department_id: UUID | None
    ) -> tuple[UUID, str, UUID | None] | None:
        """The requested pipeline, else the department's default, else the company default:
        (id, name, department_id)."""
        qs = PipelineRecord.objects.using(self.using)
        row = None
        if pipeline_id is not None:
            row = qs.filter(id=pipeline_id).first()
        else:
            if department_id is not None:
                row = qs.filter(department_id=department_id, is_default=True).first()
            row = row or qs.filter(department_id__isnull=True, is_default=True).first()
            row = row or qs.filter(department_id__isnull=True).order_by("created_at").first()
        return None if row is None else (row.id, row.name, row.department_id)

    def _deals(self, pipeline_id: UUID, owner_ids: list[UUID] | None) -> QuerySet[DealRecord]:
        qs = DealRecord.objects.using(self.using).filter(pipeline_id=pipeline_id)
        return qs if owner_ids is None else qs.filter(owner_user_id__in=owner_ids)

    def funnel_stages(
        self,
        pipeline_id: UUID,
        owner_ids: list[UUID] | None,
        *,
        period: tuple[datetime, datetime],
        previous: tuple[datetime, datetime],
        now: datetime,
        stale_days: int,
    ) -> tuple[list[StageStat], dict[str, int]]:
        stages = [
            StageStat(stage_id=s.id, name=s.name, kind=s.kind, position=s.position)
            for s in StageRecord.objects.using(self.using)
            .filter(pipeline_id=pipeline_id, is_active=True)
            .order_by("position")
        ]
        by_id = {s.stage_id: s for s in stages}
        all_stage_pos = dict(
            StageRecord.objects.using(self.using)
            .filter(pipeline_id=pipeline_id)
            .values_list("id", "position")
        )
        kinds = dict(
            StageRecord.objects.using(self.using)
            .filter(pipeline_id=pipeline_id)
            .values_list("id", "kind")
        )
        deals = self._deals(pipeline_id, owner_ids)

        # Open deals now: count, money (company currency only), age on the current stage.
        open_rows = list(
            deals.filter(status="open").values("id", "stage_id", "amount", "currency", "created_at")
        )
        open_ids = [r["id"] for r in open_rows]
        entered: dict[UUID, datetime] = {}
        for change in (
            DealStageChangeRecord.objects.using(self.using)
            .filter(deal_id__in=open_ids)
            .order_by("created_at")
            .values("deal_id", "to_stage_id", "created_at")
        ):
            entered[change["deal_id"]] = change["created_at"]  # the latest wins
        ages: dict[UUID, list[Decimal]] = defaultdict(list)
        for r in open_rows:
            stage = by_id.get(r["stage_id"])
            if stage is None:
                continue
            stage.open_deals += 1
            if r["currency"] == self.currency:
                stage.open_amount += r["amount"]
            since = entered.get(r["id"], r["created_at"])
            days = Decimal((now - since).total_seconds()) / Decimal(86400)
            ages[stage.stage_id].append(days)
            if days > stale_days:
                stage.stale_deals += 1
        for stage_id, values in ages.items():
            by_id[stage_id].median_age_days = Decimal(median(values)).quantize(Decimal("0.1"))

        # Entering stages in the period / the previous period; moving on afterwards.
        self._reach(deals, by_id, all_stage_pos, kinds, period, previous)

        start, end = period
        p_start, p_end = previous
        closed = deals.filter(closed_at__isnull=False)
        totals = {
            "won": closed.filter(status="won", closed_at__gte=start, closed_at__lt=end).count(),
            "lost": closed.filter(status="lost", closed_at__gte=start, closed_at__lt=end).count(),
            "won_previous": closed.filter(
                status="won", closed_at__gte=p_start, closed_at__lt=p_end
            ).count(),
            "lost_previous": closed.filter(
                status="lost", closed_at__gte=p_start, closed_at__lt=p_end
            ).count(),
        }
        return stages, totals

    def _reach(
        self,
        deals: QuerySet[DealRecord],
        by_id: dict[UUID, StageStat],
        positions: dict[UUID, int],
        kinds: dict[UUID, str],
        period: tuple[datetime, datetime],
        previous: tuple[datetime, datetime],
    ) -> None:
        start, end = period
        p_start = previous[0]
        # Deals that could have entered a stage since the previous period began.
        rows = list(deals.filter(Q(created_at__lt=end)).values("id", "stage_id", "created_at"))
        ids = [r["id"] for r in rows]
        changes: dict[UUID, list[tuple[datetime, UUID | None, UUID]]] = defaultdict(list)
        for c in (
            DealStageChangeRecord.objects.using(self.using)
            .filter(deal_id__in=ids)
            .order_by("created_at")
            .values("deal_id", "created_at", "from_stage_id", "to_stage_id")
        ):
            changes[c["deal_id"]].append((c["created_at"], c["from_stage_id"], c["to_stage_id"]))
        for r in rows:
            history = changes.get(r["id"], [])
            initial = history[0][1] if history and history[0][1] else r["stage_id"]
            visits = [(r["created_at"], initial)] + [(at, to) for at, _f, to in history]
            for index, (at, stage_id) in enumerate(visits):
                stage = by_id.get(stage_id)
                if stage is None:
                    continue
                if p_start <= at < start:
                    stage.reached_previous += 1
                if not (start <= at < end):
                    continue
                stage.reached += 1
                later = visits[index + 1 :]
                if any(
                    kinds.get(to) == "won"
                    or (kinds.get(to) == "open" and positions.get(to, -1) > stage.position)
                    for _at, to in later
                ):
                    stage.moved_on += 1

    # --- losses ---------------------------------------------------------------

    def lost_reasons(
        self, owner_ids: list[UUID] | None, start: datetime, end: datetime, tz: ZoneInfo
    ) -> tuple[list[ReasonCount], int]:
        qs = DealRecord.objects.using(self.using).filter(
            status="lost", closed_at__gte=start, closed_at__lt=end
        )
        if owner_ids is not None:
            qs = qs.filter(owner_user_id__in=owner_ids)
        names = dict(LossReasonRecord.objects.using(self.using).values_list("id", "name"))
        rows = []
        for d in qs.values("loss_reason_id", "lost_reason", "closed_at"):
            if d["closed_at"] is None:  # pragma: no cover - lost deals always have closed_at
                continue
            reason = (
                names.get(d["loss_reason_id"])
                if d["loss_reason_id"]
                else (d["lost_reason"] or "").strip()
            ) or NO_REASON
            rows.append((reason, d["closed_at"].astimezone(tz).strftime("%Y-%m")))
        return _reasons(rows), len(rows)

    def lost_count(self, owner_ids: list[UUID] | None, start: datetime, end: datetime) -> int:
        qs = DealRecord.objects.using(self.using).filter(
            status="lost", closed_at__gte=start, closed_at__lt=end
        )
        return (qs if owner_ids is None else qs.filter(owner_user_id__in=owner_ids)).count()

    def _meetings(
        self, owner_ids: list[UUID] | None, start: datetime, end: datetime
    ) -> QuerySet[MeetingRecord]:
        qs = MeetingRecord.objects.using(self.using).filter(
            scheduled_start__gte=start, scheduled_start__lt=end
        )
        return qs if owner_ids is None else qs.filter(owner_user_id__in=owner_ids)

    def meeting_reasons(
        self, owner_ids: list[UUID] | None, start: datetime, end: datetime, tz: ZoneInfo
    ) -> MeetingReasons:
        qs = self._meetings(owner_ids, start, end)
        counts = qs.aggregate(
            total=Count("id"),
            cancelled=Count("id", filter=Q(status="cancelled")),
            no_show=Count("id", filter=Q(status="no_show")),
        )
        cancel_rows = [
            (
                (m["cancel_reason"] or "").strip() or NO_REASON,
                m["scheduled_start"].astimezone(tz).strftime("%Y-%m"),
            )
            for m in qs.filter(status="cancelled").values("cancel_reason", "scheduled_start")
        ]
        no_show_rows = [
            (
                (m["outcome_reason"] or "").strip() or NO_REASON,
                m["scheduled_start"].astimezone(tz).strftime("%Y-%m"),
            )
            for m in qs.filter(status="no_show").values("outcome_reason", "scheduled_start")
        ]
        return MeetingReasons(
            total=counts["total"],
            cancelled=counts["cancelled"],
            no_show=counts["no_show"],
            cancel_reasons=_reasons(cancel_rows),
            no_show_reasons=_reasons(no_show_rows),
        )

    def meeting_counts(
        self, owner_ids: list[UUID] | None, start: datetime, end: datetime
    ) -> tuple[int, int]:
        counts = self._meetings(owner_ids, start, end).aggregate(
            cancelled=Count("id", filter=Q(status="cancelled")),
            no_show=Count("id", filter=Q(status="no_show")),
        )
        return counts["cancelled"], counts["no_show"]

    def lead_response(
        self, owner_ids: list[UUID] | None, start: datetime, end: datetime
    ) -> LeadResponse:
        qs = LeadRecord.objects.using(self.using).filter(created_at__gte=start, created_at__lt=end)
        if owner_ids is not None:
            qs = qs.filter(owner_user_id__in=owner_ids)
        leads = {r["id"]: r["created_at"] for r in qs.values("id", "created_at")}
        first: dict[UUID, datetime] = {}
        for row in (
            ActivityRecord.objects.using(self.using)
            .filter(subject_type="lead", subject_id__in=list(leads))
            .values("subject_id")
            .annotate(at=Min("created_at"))
        ):
            first[row["subject_id"]] = row["at"]
        for row in (
            AuditRecord.objects.using(self.using)
            .filter(
                resource_type="lead",
                resource_id__in=[str(i) for i in leads],
                action__in=_RESPONSE_ACTIONS,
                actor_user_id__isnull=False,
            )
            .values("resource_id")
            .annotate(at=Min("occurred_at"))
        ):
            lead_id = UUID(row["resource_id"])
            if lead_id not in first or row["at"] < first[lead_id]:
                first[lead_id] = row["at"]
        minutes = [
            Decimal(max((first[i] - created).total_seconds(), 0)) / Decimal(60)
            for i, created in leads.items()
            if i in first
        ]
        return LeadResponse(
            leads=len(leads),
            responded=len(minutes),
            median_minutes=Decimal(median(minutes)).quantize(Decimal("0.1")) if minutes else None,
            within_15_min=sum(1 for m in minutes if m <= 15),
            within_60_min=sum(1 for m in minutes if m <= 60),
        )

    # --- tasks as of a past day ---------------------------------------------------

    def tasks_as_of(self, user_id: UUID, day_end: datetime, day: date) -> tuple[int, int]:
        """(open, overdue) tasks of the user at the end of `day`: created before the end of the
        day and not completed by then. Cancelled tasks are left out (their cancel time is not
        stored)."""
        qs = (
            ActivityRecord.objects.using(self.using)
            .filter(kind="task", assignee_user_id=user_id, created_at__lt=day_end)
            .exclude(task_status="cancelled")
        )
        open_then = qs.filter(Q(completed_at__isnull=True) | Q(completed_at__gte=day_end))
        return open_then.count(), open_then.filter(due_date__lt=day).count()
