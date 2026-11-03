"""Head-of-sales cabinet use cases. Read-only aggregation; RBAC enforced here.

Access needs team_cabinet.view (head of sales, director, owner/admin) — managers,
lead-callers and office staff do not see it. A head of sales (TEAM scope) sees only
their own department; a director (ALL) sees any department or the whole company.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Scope
from modules.team_cabinet.application.ports import CabinetReader, TeamRoster
from modules.team_cabinet.domain.board import (
    DepartmentTotals,
    ManagerDay,
    Member,
    RawDay,
    ScoreThresholds,
    TrafficThresholds,
    evaluate_day_score,
    evaluate_traffic_light,
)
from modules.team_cabinet.domain.period import Discipline, PeriodMetrics, average
from modules.team_cabinet.domain.plan_fact import PlanFact, PlanFactRow
from shared.domain.errors import NotFoundError


@dataclass(frozen=True, slots=True)
class TodayBoard:
    as_of: datetime
    managers: list[ManagerDay]
    totals: DepartmentTotals


@dataclass(frozen=True, slots=True)
class MemberCard:
    member: Member
    start: date
    end: date
    current: PeriodMetrics
    previous: PeriodMetrics
    department_average: dict[str, Decimal]
    discipline: Discipline
    month: str
    plan_sales: Decimal | None
    month_sales: int
    days_without_activity: list[date]
    absent_days: list[date]


@dataclass(frozen=True, slots=True)
class TeamReportRow:
    member: Member
    metrics: PeriodMetrics
    discipline: Discipline
    plan_sales: Decimal | None
    month_sales: int


@dataclass(frozen=True, slots=True)
class TeamReport:
    start: date
    end: date
    department_id: UUID | None
    rows: list[TeamReportRow]


class TeamCabinetService:
    def __init__(
        self,
        roster: TeamRoster,
        reader: CabinetReader,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._roster = roster
        self._reader = reader
        self._policy = policy or AccessPolicy()

    def today_board(
        self,
        principal: Principal,
        *,
        timezone_name: str,
        thresholds: TrafficThresholds,
        department_id: UUID | None = None,
    ) -> TodayBoard:
        scope = self._policy.require(principal, Permission.TEAM_CABINET_VIEW)
        # A head of sales is pinned to their own department; a director may choose.
        if scope is not Scope.ALL:
            department_id = principal.department_id

        tz = ZoneInfo(timezone_name)
        now = datetime.now(tz)
        today = now.date()
        day_start = datetime(today.year, today.month, today.day, tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        outcome_cutoff = now - timedelta(minutes=thresholds.meeting_outcome_minutes)
        morning_cutoff = day_start + timedelta(hours=thresholds.morning_cutoff_hour)

        members = self._roster.members(principal.tenant_id, department_id)
        user_ids = [m.user_id for m in members]
        raw = self._reader.day_metrics(
            user_ids,
            day_start=day_start,
            day_end=day_end,
            now=now,
            outcome_cutoff=outcome_cutoff,
            morning_cutoff=morning_cutoff,
            today=today,
        )
        absent = self._reader.absent_user_ids(user_ids, today)
        month = f"{today.year:04d}-{today.month:02d}"
        days_total = calendar.monthrange(today.year, today.month)[1]
        targets = self._reader.monthly_sales_targets(user_ids, month)
        sales = self._reader.monthly_sales_counts(user_ids, today.replace(day=1), today)

        managers = []
        for m in members:
            day = member_day(m, raw.get(m.user_id), absent)
            day.plan_sales = targets.get(m.user_id)
            day.sales_month = sales.get(m.user_id, 0)
            # No score for a silent day or a planned absence: there is nothing to grade yet.
            if day.has_activity and not day.on_absence:
                day.score = evaluate_day_score(
                    day, days_elapsed=today.day, days_total=days_total, thresholds=ScoreThresholds()
                )
            managers.append(self._score(day, now.hour, thresholds))
        return TodayBoard(as_of=now, managers=managers, totals=DepartmentTotals.of(managers))

    def inactivity(
        self,
        principal: Principal,
        *,
        timezone_name: str,
        thresholds: TrafficThresholds,
        department_id: UUID | None = None,
    ) -> TodayBoard:
        board = self.today_board(
            principal,
            timezone_name=timezone_name,
            thresholds=thresholds,
            department_id=department_id,
        )
        silent = [m for m in board.managers if "no_activity" in m.red_reasons]
        return TodayBoard(as_of=board.as_of, managers=silent, totals=DepartmentTotals.of(silent))

    def plan_fact(
        self,
        principal: Principal,
        *,
        month: str,
        timezone_name: str,
        department_id: UUID | None = None,
    ) -> PlanFact:
        scope = self._policy.require(principal, Permission.TEAM_CABINET_VIEW)
        if scope is not Scope.ALL:
            department_id = principal.department_id

        year, mon = int(month[:4]), int(month[5:7])
        days_in_month = calendar.monthrange(year, mon)[1]
        month_start = date(year, mon, 1)
        month_end = date(year, mon, days_in_month)
        today = datetime.now(ZoneInfo(timezone_name)).date()
        if today < month_start:
            elapsed = 0
        elif today > month_end:
            elapsed = days_in_month
        else:
            elapsed = today.day

        members = self._roster.members(principal.tenant_id, department_id)
        user_ids = [m.user_id for m in members]
        targets = self._reader.monthly_sales_targets(user_ids, month)
        received = self._reader.monthly_received(user_ids, month_start, month_end)
        counts = self._reader.monthly_sales_counts(user_ids, month_start, month_end)

        rows = [
            PlanFactRow(
                user_id=m.user_id,
                username=m.username,
                department_id=m.department_id,
                product_role=m.product_role,
                plan_sales=targets.get(m.user_id, Decimal(0)),
                sales_actual=counts.get(m.user_id, 0),
                received_actual=received.get(m.user_id, Decimal(0)),
                working_days_total=days_in_month,
                working_days_elapsed=elapsed,
            )
            for m in members
        ]
        return PlanFact(
            month=month,
            working_days_total=days_in_month,
            working_days_elapsed=elapsed,
            rows=rows,
            department_id=department_id,
            department_plan_sales=(
                self._reader.department_sales_target(department_id, month)
                if department_id is not None
                else None
            ),
        )

    def member_card(
        self,
        principal: Principal,
        user_id: UUID,
        *,
        start: date,
        end: date,
        timezone_name: str,
    ) -> MemberCard:
        """One manager over a period, with the previous period of the same length and the
        department average. A head of sales sees only members of their own department."""
        scope = self._policy.require(principal, Permission.TEAM_CABINET_VIEW)
        everyone = self._roster.members(principal.tenant_id, None)
        member = next((m for m in everyone if m.user_id == user_id), None)
        if member is None or (
            scope is not Scope.ALL and member.department_id != principal.department_id
        ):
            raise NotFoundError("No such member in your department.")
        peers = [m for m in everyone if m.department_id == member.department_id]
        peer_ids = [m.user_id for m in peers]
        length = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=length - 1)
        current = self._reader.period_metrics(peer_ids, start, end, tz_name=timezone_name)
        previous = self._reader.period_metrics(
            [user_id], prev_start, prev_end, tz_name=timezone_name
        )
        tz = ZoneInfo(timezone_name)
        now = datetime.now(tz)
        today = now.date()
        tails = self._reader.discipline([user_id], now=now, today=today)
        month = f"{end.year:04d}-{end.month:02d}"
        month_start = end.replace(day=1)
        plan = self._reader.monthly_sales_targets([user_id], month).get(user_id)
        month_sales = self._reader.monthly_sales_counts([user_id], month_start, end).get(user_id, 0)
        absent = {
            day
            for day in (start + timedelta(days=i) for i in range(length))
            if user_id in self._reader.absent_user_ids([user_id], day)
        }
        mine = current[user_id]
        silent = [
            day
            for day in (start + timedelta(days=i) for i in range(length))
            if day <= today and day.weekday() < 6 and day not in absent and not mine.active_on(day)
        ]
        return MemberCard(
            member=member,
            start=start,
            end=end,
            current=mine,
            previous=previous[user_id],
            department_average=average(list(current.values())),
            discipline=tails[user_id],
            month=month,
            plan_sales=plan,
            month_sales=month_sales,
            days_without_activity=silent,
            absent_days=sorted(absent),
        )

    def team_report(
        self,
        principal: Principal,
        *,
        start: date,
        end: date,
        timezone_name: str,
        department_id: UUID | None = None,
    ) -> TeamReport:
        """Per-member figures for a day, week or month plus department totals."""
        scope = self._policy.require(principal, Permission.TEAM_CABINET_VIEW)
        if scope is not Scope.ALL:
            department_id = principal.department_id
        members = self._roster.members(principal.tenant_id, department_id)
        ids = [m.user_id for m in members]
        metrics = self._reader.period_metrics(ids, start, end, tz_name=timezone_name)
        tz = ZoneInfo(timezone_name)
        now = datetime.now(tz)
        tails = self._reader.discipline(ids, now=now, today=now.date())
        month = f"{end.year:04d}-{end.month:02d}"
        targets = self._reader.monthly_sales_targets(ids, month)
        month_sales = self._reader.monthly_sales_counts(ids, end.replace(day=1), end)
        rows = [
            TeamReportRow(
                member=m,
                metrics=metrics[m.user_id],
                discipline=tails[m.user_id],
                plan_sales=targets.get(m.user_id),
                month_sales=month_sales.get(m.user_id, 0),
            )
            for m in members
        ]
        return TeamReport(start=start, end=end, department_id=department_id, rows=rows)

    @staticmethod
    def _score(day: ManagerDay, now_hour: int, thresholds: TrafficThresholds) -> ManagerDay:
        # No working-day calendar yet, so every day is treated as a working day.
        evaluate_traffic_light(day, now_hour=now_hour, thresholds=thresholds, is_working_day=True)
        return day


def member_day(member: Member, raw: RawDay | None, absent: set[UUID]) -> ManagerDay:
    r = raw or RawDay()
    return ManagerDay(
        user_id=member.user_id,
        username=member.username,
        department_id=member.department_id,
        product_role=member.product_role,
        calls=r.calls,
        meetings_total=r.meetings_total,
        meetings_confirmed=r.meetings_confirmed,
        meetings_held=r.meetings_held,
        meetings_no_show=r.meetings_no_show,
        meetings_overdue_outcome=r.meetings_overdue_outcome,
        morning_meetings=r.morning_meetings,
        morning_confirmed=r.morning_confirmed,
        payments_count=r.payments_count,
        received=r.received,
        open_tasks=r.open_tasks,
        overdue_tasks=r.overdue_tasks,
        reschedules_without_date=r.reschedules_without_date,
        meetings_to_confirm=r.meetings_to_confirm,
        next_to_confirm_at=r.next_to_confirm_at,
        meetings_cancelled=r.meetings_cancelled,
        tasks_due_today=r.tasks_due_today,
        tasks_done_today=r.tasks_done_today,
        on_absence=member.user_id in absent,
    )
