"""Sends the automatic morning/evening reports and inactivity alerts for one company.

Run periodically (every few minutes) with the company-local clock. A report is sent once
its local time has come (see ``ReportSchedule.is_due``); a per-company marker records that
the day's report went out, and every notification is keyed by (kind, local day, recipient),
so restarts and overlapping runs never send a second report the same day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from modules.notifications.application.ports import DailyFiguresSource, ReportRoster, ReportRuns
from modules.notifications.application.service import NotificationService
from modules.notifications.domain.daily_reports import (
    MANAGER,
    MessageKind,
    ReportInput,
    ReportKind,
    ReportSchedule,
    evening_messages,
    morning_messages,
)
from modules.notifications.domain.notification import NotificationCategory

_CATEGORY = {
    MessageKind.MORNING: NotificationCategory.REPORT,
    MessageKind.EVENING: NotificationCategory.REPORT,
    MessageKind.INACTIVITY: NotificationCategory.INACTIVITY,
}


@dataclass(frozen=True, slots=True)
class DailyReportRun:
    morning: int = 0
    evening: int = 0
    inactivity: int = 0


class DailyReportService:
    def __init__(
        self,
        notifications: NotificationService,
        roster: ReportRoster,
        figures: DailyFiguresSource,
        runs: ReportRuns,
        *,
        currency: str,
    ) -> None:
        self._notifications = notifications
        self._roster = roster
        self._figures = figures
        self._runs = runs
        self._currency = currency

    def run(self, local_now: datetime, schedule: ReportSchedule) -> DailyReportRun:
        """``local_now`` is the company-local, timezone-aware current time."""
        if local_now.tzinfo is None:
            raise ValueError("local_now must be timezone-aware (company timezone).")
        day = local_now.date()
        due = [k for k in ReportKind if schedule.is_due(k, local_now)]
        if not due:
            return DailyReportRun()
        due = [k for k in due if k not in self._runs.done(day)]
        created = dict.fromkeys(MessageKind, 0)
        for kind in due:
            for message_kind, n in self._send(kind, local_now).items():
                created[message_kind] += n
            # Marked only after every message was handled: a failed run is retried by the
            # next beat, and the keyed notifications already created are not repeated.
            self._runs.mark_done(kind, day)
        return DailyReportRun(
            morning=created[MessageKind.MORNING],
            evening=created[MessageKind.EVENING],
            inactivity=created[MessageKind.INACTIVITY],
        )

    def _send(self, kind: ReportKind, local_now: datetime) -> dict[MessageKind, int]:
        day = local_now.date()
        staff = self._roster.staff()
        user_ids = [p.user_id for p in staff]
        absent = self._figures.absent(user_ids, day)
        managers = [p.user_id for p in staff if p.role == MANAGER and p.user_id not in absent]
        data = ReportInput(day=day, currency=self._currency, staff=staff, absent=absent)
        if kind is ReportKind.MORNING:
            data.morning.update(self._figures.morning(managers, day=day, now=local_now))
            messages = morning_messages(data)
        else:
            data.evening.update(self._figures.evening(managers, day=day, now=local_now))
            messages = evening_messages(data)
        created = dict.fromkeys(MessageKind, 0)
        for message in messages:
            if self._notifications.notify(
                recipient_user_id=message.recipient_user_id,
                category=_CATEGORY[message.kind],
                title=message.title,
                body=message.body,
                source_event_id=message.key,
                link=message.link,
            ):
                created[message.kind] += 1
        return created
