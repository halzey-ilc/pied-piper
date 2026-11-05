"""The planned next step of deals: their nearest open task with a due date.

Owner decision 03.11.2026: the next step is a task (title, due date, assignee). The deal's own
free-text ``next_step`` predates that rule; lists show the task first.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from uuid import UUID

from modules.activities.infrastructure.models import ActivityRecord


def next_tasks(using: str, deal_ids: Iterable[UUID]) -> dict[UUID, tuple[str, date]]:
    """deal id -> (title, due date) of its open task due soonest. One query."""
    ids = list(deal_ids)
    if not ids:
        return {}
    rows = (
        ActivityRecord.objects.using(using)
        .filter(
            kind="task",
            subject_type="deal",
            subject_id__in=ids,
            task_status="open",
            due_date__isnull=False,
        )
        .order_by("subject_id", "due_date", "created_at")
        .values_list("subject_id", "title", "due_date")
    )
    found: dict[UUID, tuple[str, date]] = {}
    for subject_id, title, due in rows:
        if due is not None:  # filtered above; narrows the type
            found.setdefault(subject_id, (title or "", due))
    return found
