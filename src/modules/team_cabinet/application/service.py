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
from modules.team_cabinet.application.ports import (
    ActionsReader,
    AnalyticsReader,
    CabinetReader,
    TeamRoster,
)
from modules.team_cabinet.domain.actions import (
    ACTION_ORDER,
    FIELD_ROLES,
    ActionGroup,
    ActionItem,
    ActionKind,
    ActionThresholds,
    TodayActions,
    manager_reasons,
)
from modules.team_cabinet.domain.analytics import (
    DayScorePoint,
    FunnelReport,
    LossReport,
    ScoreHistory,
    finish_funnel,
)
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
from shared.domain.errors import DomainValidationError, NotFoundError


@dataclass(frozen=True, slots=True)
class TodayBoard:
    as_of: datetime
    managers: list[ManagerDay]
    totals: DepartmentTotals
    day: date | None = None  # the company-local day the board describes


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
        analytics: AnalyticsReader | None = None,
        actions: ActionsReader | None = None,
    ) -> None:
        self._roster = roster
        self._reader = reader
        self._policy = policy or AccessPolicy()
        self._analytics = analytics
        self._actions = actions

    def today_board(
        self,
        principal: Principal,
        *,
        timezone_name: str,
        thresholds: TrafficThresholds,
        department_id: UUID | None = None,
        on: date | None = None,
    ) -> TodayBoard:
        """Today's board, or a past day ``on`` (e.g. yesterday's score early in the morning).

        A past day is read as of its end: its calls, meetings, payments and score; tasks are
        counted as they stand now against that day's date."""
        scope = self._policy.require(principal, Permission.TEAM_CABINET_VIEW)
        # A head of sales is pinned to their own department; a director may choose.
        if scope is not Scope.ALL:
            department_id = principal.department_id

        tz = ZoneInfo(timezone_name)
        now = datetime.now(tz)
        today = now.date()
        if on is not None and on > today:
            raise DomainValidationError("The day must not be in the future.")
        if on is not None and on < today:
            today = on
            now = datetime(on.year, on.month, on.day, tzinfo=tz) + timedelta(days=1)
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
        return TodayBoard(
            as_of=min(now, datetime.now(tz)),
            managers=managers,
            totals=DepartmentTotals.of(managers),
            day=today,
        )

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

    # --- «Что делать сегодня» -------------------------------------------------

    def today_actions(
        self,
        principal: Principal,
        board: TodayBoard,
        *,
        timezone_name: str,
        limit: int,
        department_id: UUID | None = None,
    ) -> TodayActions:
        """Prioritised action groups for a head of sales (their department) or a director
        (the whole company or one department). ``board`` is today's board for the same scope:
        its rows give who is silent today."""
        owners, department_id = self._owners(principal, department_id)
        if self._actions is None:  # pragma: no cover - wiring error
            raise RuntimeError("Actions reader is not configured.")
        reader = self._actions
        tz = ZoneInfo(timezone_name)
        now = datetime.now(tz)
        today = now.date()
        day_start = datetime(today.year, today.month, today.day, tzinfo=tz)
        thresholds = ActionThresholds(stalled_deal_days=reader.stalled_deal_days())

        groups: dict[ActionKind, ActionGroup] = {
            ActionKind.PAYMENTS_TO_VERIFY: reader.payments_to_verify(owners, now=now, limit=limit),
            ActionKind.OVERDUE_TASKS: reader.overdue_tasks(owners, today=today, limit=limit),
            ActionKind.TASKS_DUE_SOON: reader.tasks_due_soon(owners, today=today, limit=limit),
            ActionKind.MEETINGS_WITHOUT_OUTCOME: reader.meetings_without_outcome(
                owners, now=now, limit=limit
            ),
            ActionKind.DEALS_WITHOUT_NEXT_STEP: reader.deals_without_next_step(
                owners, now=now, limit=limit
            ),
            ActionKind.DEALS_TO_PUSH: reader.deals_to_push(
                owners, now=now, stalled_days=thresholds.stalled_deal_days, limit=limit
            ),
            ActionKind.LEADS_WITHOUT_CONTACT: reader.leads_without_contact(
                owners, now=now, response_minutes=thresholds.lead_response_minutes, limit=limit
            ),
            ActionKind.MANAGERS_WITHOUT_ACTIVITY: self._silent_managers(
                board, reader, day_start=day_start, limit=limit
            ),
        }
        names = {m.user_id: m.username for m in self._roster.members(principal.tenant_id, None)}
        for group in groups.values():
            for item in group.items:
                if item.manager_id is not None:
                    item.manager_username = names.get(item.manager_id, "")
        return TodayActions(
            day=today,
            as_of=now,
            limit=limit,
            thresholds=thresholds,
            department_id=department_id,
            groups=[groups[kind] for kind in ACTION_ORDER],
            usernames=names,
        )

    @staticmethod
    def _silent_managers(
        board: TodayBoard, reader: ActionsReader, *, day_start: datetime, limit: int
    ) -> ActionGroup:
        """Sales managers and lead callers not on a planned absence who did not start a
        shift today or have no calls, meetings or payments today."""
        field = [m for m in board.managers if m.product_role in FIELD_ROLES and not m.on_absence]
        on_shift = reader.on_shift(
            [m.user_id for m in field], day_start=day_start, day_end=day_start + timedelta(days=1)
        )
        group = ActionGroup(ActionKind.MANAGERS_WITHOUT_ACTIVITY)
        for m in field:
            reasons = manager_reasons(on_shift=m.user_id in on_shift, has_activity=m.has_activity)
            if not reasons:
                continue
            group.count += 1
            group.by_manager[m.user_id] = 1
            if len(group.items) < limit:
                group.items.append(
                    ActionItem(
                        id=m.user_id, title=m.username, manager_id=m.user_id, reasons=reasons
                    )
                )
        return group

    # --- analytics ------------------------------------------------------------

    def _owners(
        self, principal: Principal, department_id: UUID | None
    ) -> tuple[list[UUID] | None, UUID | None]:
        """Whose records the caller analyses: a head of sales — their department; a director —
        one department (department_id) or the whole company (None = no owner filter)."""
        scope = self._policy.require(principal, Permission.TEAM_CABINET_VIEW)
        if scope is not Scope.ALL:
            department_id = principal.department_id
        if department_id is None and scope is Scope.ALL:
            return None, None
        members = self._roster.members(principal.tenant_id, department_id)
        return [m.user_id for m in members], department_id

    def _analytics_reader(self) -> AnalyticsReader:
        if self._analytics is None:  # pragma: no cover - wiring error
            raise RuntimeError("Analytics reader is not configured.")
        return self._analytics

    def funnel(
        self,
        principal: Principal,
        *,
        start: date,
        end: date,
        timezone_name: str,
        stale_days: int,
        pipeline_id: UUID | None = None,
        department_id: UUID | None = None,
    ) -> FunnelReport:
        """Stages of one pipeline: open deals with money and age now; deals that entered each
        stage in [start, end] (and in the previous period of equal length), how many of them
        moved on, the conversion and the bottleneck."""
        owners, department_id = self._owners(principal, department_id)
        reader = self._analytics_reader()
        found = reader.pipeline_for(pipeline_id, department_id)
        scope = self._policy.scope_for(principal, Permission.TEAM_CABINET_VIEW)
        if found is None or (
            scope is not Scope.ALL and found[2] not in (None, principal.department_id)
        ):
            raise NotFoundError("Pipeline not found.")
        tz = ZoneInfo(timezone_name)
        period, previous, prev_start, prev_end = _windows(start, end, tz)
        stages, totals = reader.funnel_stages(
            found[0],
            owners,
            period=period,
            previous=previous,
            now=datetime.now(tz),
            stale_days=stale_days,
        )
        finish_funnel(stages)
        return FunnelReport(
            pipeline_id=found[0],
            pipeline_name=found[1],
            start=start,
            end=end,
            previous_start=prev_start,
            previous_end=prev_end,
            stale_days=stale_days,
            stages=stages,
            won=totals["won"],
            lost=totals["lost"],
            won_previous=totals["won_previous"],
            lost_previous=totals["lost_previous"],
        )

    def losses(
        self,
        principal: Principal,
        *,
        start: date,
        end: date,
        timezone_name: str,
        department_id: UUID | None = None,
    ) -> LossReport:
        """Lost deals by reason and month, meeting cancellations and no-shows by reason, and
        how fast new leads got a first response — for the period and the previous one."""
        owners, _ = self._owners(principal, department_id)
        reader = self._analytics_reader()
        tz = ZoneInfo(timezone_name)
        (p_from, p_to), (q_from, q_to), prev_start, prev_end = _windows(start, end, tz)
        reasons, lost = reader.lost_reasons(owners, p_from, p_to, tz)
        meetings = reader.meeting_reasons(owners, p_from, p_to, tz)
        cancelled_prev, no_show_prev = reader.meeting_counts(owners, q_from, q_to)
        months = []
        cursor = start.replace(day=1)
        while cursor <= end:
            months.append(f"{cursor.year:04d}-{cursor.month:02d}")
            cursor = (cursor + timedelta(days=32)).replace(day=1)
        return LossReport(
            start=start,
            end=end,
            previous_start=prev_start,
            previous_end=prev_end,
            months=months,
            lost_deals=lost,
            lost_previous=reader.lost_count(owners, q_from, q_to),
            loss_reasons=reasons,
            meetings_total=meetings.total,
            meetings_cancelled=meetings.cancelled,
            meetings_no_show=meetings.no_show,
            cancelled_previous=cancelled_prev,
            no_show_previous=no_show_prev,
            cancel_reasons=meetings.cancel_reasons,
            no_show_reasons=meetings.no_show_reasons,
            lead_response=reader.lead_response(owners, p_from, p_to),
        )

    def score_history(
        self,
        principal: Principal,
        user_id: UUID,
        *,
        start: date,
        end: date,
        timezone_name: str,
        thresholds: TrafficThresholds,
    ) -> ScoreHistory:
        """The day score of one member for every day of the period, computed with the same
        rule as today's board. Meetings, payments and sales are the day's own; open and overdue
        tasks are as they stood at the end of that day. No score on a silent day, a planned
        absence or a day not yet over."""
        scope = self._policy.require(principal, Permission.TEAM_CABINET_VIEW)
        member = next(
            (m for m in self._roster.members(principal.tenant_id, None) if m.user_id == user_id),
            None,
        )
        if member is None or (
            scope is not Scope.ALL and member.department_id != principal.department_id
        ):
            raise NotFoundError("No such member in your department.")
        reader = self._analytics_reader()
        tz = ZoneInfo(timezone_name)
        now = datetime.now(tz)
        points: list[DayScorePoint] = []
        day = start
        while day <= end:
            points.append(self._day_point(member, day, now, tz, thresholds, reader))
            day += timedelta(days=1)
        return ScoreHistory(user_id=user_id, start=start, end=end, points=points, generated_at=now)

    def _day_point(
        self,
        member: Member,
        day: date,
        now: datetime,
        tz: ZoneInfo,
        thresholds: TrafficThresholds,
        reader: AnalyticsReader,
    ) -> DayScorePoint:
        if day > now.date():
            return DayScorePoint(day=day, points=None, grade=None, reason=None)
        day_start = datetime(day.year, day.month, day.day, tzinfo=tz)
        day_end = min(day_start + timedelta(days=1), now)
        uid = member.user_id
        raw = self._reader.day_metrics(
            [uid],
            day_start=day_start,
            day_end=day_end,
            now=day_end,
            outcome_cutoff=day_end - timedelta(minutes=thresholds.meeting_outcome_minutes),
            morning_cutoff=day_start + timedelta(hours=thresholds.morning_cutoff_hour),
            today=day,
        ).get(uid)
        absent = self._reader.absent_user_ids([uid], day)
        point = member_day(member, raw, absent)
        point.open_tasks, point.overdue_tasks = reader.tasks_as_of(uid, day_end, day)
        if point.on_absence:
            return DayScorePoint(day=day, points=None, grade=None, reason="absent")
        if not point.has_activity:
            return DayScorePoint(day=day, points=None, grade=None, reason="silent")
        month = f"{day.year:04d}-{day.month:02d}"
        point.plan_sales = self._reader.monthly_sales_targets([uid], month).get(uid)
        point.sales_month = self._reader.monthly_sales_counts([uid], day.replace(day=1), day).get(
            uid, 0
        )
        score = evaluate_day_score(
            point,
            days_elapsed=day.day,
            days_total=calendar.monthrange(day.year, day.month)[1],
            thresholds=ScoreThresholds(),
        )
        return DayScorePoint(day=day, points=score.points, grade=score.grade, reason=None)

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
        prepayments=r.prepayments,
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


def _windows(
    start: date, end: date, tz: ZoneInfo
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime], date, date]:
    """[start, end] in company-local days and the previous period of equal length, as
    half-open datetime ranges, plus the previous period's dates."""
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)

    def local(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=tz)

    period = (local(start), local(end) + timedelta(days=1))
    previous = (local(prev_start), local(prev_end) + timedelta(days=1))
    return period, previous, prev_start, prev_end
