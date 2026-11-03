"""Creates task deadline notifications (reminders, overdue alerts, escalations) for one
company. Run periodically; repeated runs are harmless (notifications are keyed)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from modules.notifications.application.ports import (
    DepartmentHeads,
    PreferenceRepository,
    TaskDeadlineSource,
)
from modules.notifications.application.service import NotificationService
from modules.notifications.domain.deadlines import (
    ESCALATION_DAYS,
    AlertKind,
    DeadlineAlert,
    deadline_alerts,
)
from modules.notifications.domain.notification import NotificationCategory
from modules.notifications.domain.preferences import MAX_REMINDER_DAYS_BEFORE


@dataclass(frozen=True, slots=True)
class DeadlineRun:
    reminders: int = 0
    overdue: int = 0
    escalations: int = 0


def _when(days: int) -> str:
    if days == 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    return f"через {days} дн."


def _texts(alert: DeadlineAlert, subject: str | None) -> tuple[str, str]:
    title = alert.task.title or "Задача"
    about = f" · {subject}" if subject else ""
    if alert.kind is AlertKind.REMINDER:
        return (
            f"Срок задачи {_when(alert.days)}: {title}",
            f"Срок {alert.task.due_date:%d.%m.%Y}{about}",
        )
    if alert.kind is AlertKind.OVERDUE:
        return f"Задача просрочена: {title}", f"Срок был {alert.task.due_date:%d.%m.%Y}{about}"
    return (
        f"Задача сотрудника просрочена на {alert.days} дн.: {title}",
        f"Срок был {alert.task.due_date:%d.%m.%Y}{about}. Проверьте, что с ней.",  # noqa: RUF001
    )


def _link(alert: DeadlineAlert) -> str:
    task = alert.task
    if task.subject_type == "deal":
        return f"/leads/{task.subject_id}"
    if task.subject_type == "customer":
        return f"/client-base?client={task.subject_id}"
    return "/tasks"


class DeadlineAlertService:
    def __init__(
        self,
        notifications: NotificationService,
        preferences: PreferenceRepository,
        tasks: TaskDeadlineSource,
        heads: DepartmentHeads,
        *,
        escalation_days: int = ESCALATION_DAYS,
    ) -> None:
        self._notifications = notifications
        self._prefs = preferences
        self._tasks = tasks
        self._heads = heads
        self._escalation_days = escalation_days

    def run(self, today: date) -> DeadlineRun:
        open_tasks = self._tasks.open_tasks_due_until(today + timedelta(MAX_REMINDER_DAYS_BEFORE))
        if not open_tasks:
            return DeadlineRun()
        reminder_days: dict[UUID, int] = {}
        heads: dict[UUID, UUID | None] = {}

        def reminder_days_for(user_id: UUID) -> int:
            if user_id not in reminder_days:
                reminder_days[user_id] = self._prefs.get(user_id).reminder_days_before
            return reminder_days[user_id]

        def head_of(user_id: UUID) -> UUID | None:
            if user_id not in heads:
                heads[user_id] = self._heads.head_of(user_id)
            return heads[user_id]

        alerts = deadline_alerts(
            open_tasks,
            today,
            reminder_days_for=reminder_days_for,
            head_of=head_of,
            escalation_days=self._escalation_days,
        )
        subjects = self._tasks.subject_titles([a.task for a in alerts])
        created = dict.fromkeys(AlertKind, 0)
        for alert in alerts:
            title, body = _texts(alert, subjects.get(alert.task.subject_id))
            if self._notifications.notify(
                recipient_user_id=alert.recipient_user_id,
                category=NotificationCategory.TASK,
                title=title[:200],
                body=body,
                source_event_id=alert.key,
                link=_link(alert),
            ):
                created[alert.kind] += 1
        return DeadlineRun(
            reminders=created[AlertKind.REMINDER],
            overdue=created[AlertKind.OVERDUE],
            escalations=created[AlertKind.ESCALATION],
        )
