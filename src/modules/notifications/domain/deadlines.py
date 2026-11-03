"""Task deadline alerts: which reminders, overdue alerts and escalations are due today.

Pure rules over open tasks with a due date (a calendar date in the company's timezone):

- reminder: the assignee, ``reminder_days_before`` days before the due date or later, up to
  and including the due day;
- overdue: the assignee, once, the first day after the due date;
- escalation: the head of sales of the assignee's department, once the task is
  ``escalation_days`` calendar days overdue (no working-day calendar yet). Not sent when the
  assignee is that head of sales.

Each alert carries a stable key (task, kind, due date, recipient): repeated runs create no
duplicates, and moving the due date starts a new cycle.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

ESCALATION_DAYS = 2  # calendar days overdue before the head of sales is told

_KEY_NAMESPACE = uuid5(NAMESPACE_URL, "crm:notifications:task-deadlines")


class AlertKind(StrEnum):
    REMINDER = "reminder"
    OVERDUE = "overdue"
    ESCALATION = "escalation"


@dataclass(frozen=True, slots=True)
class TaskDeadline:
    task_id: UUID
    title: str
    assignee_user_id: UUID
    due_date: date
    subject_type: str  # customer | deal
    subject_id: UUID


@dataclass(frozen=True, slots=True)
class DeadlineAlert:
    kind: AlertKind
    recipient_user_id: UUID
    task: TaskDeadline
    days: int  # days until due (reminder) or days overdue (overdue, escalation)

    @property
    def key(self) -> UUID:
        """Stable id of this alert: the notification's idempotency key."""
        raw = f"{self.kind}:{self.task.task_id}:{self.task.due_date}:{self.recipient_user_id}"
        return uuid5(_KEY_NAMESPACE, raw)


def deadline_alerts(
    tasks: Iterable[TaskDeadline],
    today: date,
    *,
    reminder_days_for: Callable[[UUID], int],
    head_of: Callable[[UUID], UUID | None],
    escalation_days: int = ESCALATION_DAYS,
) -> list[DeadlineAlert]:
    alerts: list[DeadlineAlert] = []
    for task in tasks:
        days_left = (task.due_date - today).days
        if days_left >= 0:
            if days_left <= reminder_days_for(task.assignee_user_id):
                alerts.append(
                    DeadlineAlert(AlertKind.REMINDER, task.assignee_user_id, task, days_left)
                )
            continue
        overdue = -days_left
        alerts.append(DeadlineAlert(AlertKind.OVERDUE, task.assignee_user_id, task, overdue))
        if overdue >= escalation_days:
            head = head_of(task.assignee_user_id)
            if head is not None and head != task.assignee_user_id:
                alerts.append(DeadlineAlert(AlertKind.ESCALATION, head, task, overdue))
    return alerts
