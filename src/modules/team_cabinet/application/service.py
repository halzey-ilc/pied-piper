"""Head-of-sales cabinet use cases. Read-only aggregation; RBAC enforced here.

Access needs team_cabinet.view (head of sales, director, owner/admin) — managers,
lead-callers and office staff do not see it. A head of sales (TEAM scope) sees only
their own department; a director (ALL) sees any department or the whole company.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Scope
from modules.team_cabinet.application.ports import CabinetReader, TeamRoster
from modules.team_cabinet.domain.board import (
    DepartmentTotals,
    ManagerDay,
    Member,
    RawDay,
    TrafficThresholds,
    evaluate_traffic_light,
)


@dataclass(frozen=True, slots=True)
class TodayBoard:
    as_of: datetime
    managers: list[ManagerDay]
    totals: DepartmentTotals


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

        managers = [
            self._score(member_day(m, raw.get(m.user_id), absent), now.hour, thresholds)
            for m in members
        ]
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
        on_absence=member.user_id in absent,
    )
