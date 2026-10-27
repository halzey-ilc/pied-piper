from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from modules.activities.domain.activity import (
    Activity,
    ActivityLogged,
    ActivityStateError,
    CallDirection,
    SubjectRef,
    SubjectType,
    TaskCompleted,
)
from shared.domain.errors import DomainValidationError


def _subject() -> SubjectRef:
    return SubjectRef(type=SubjectType.CUSTOMER, id=uuid4())


def test_log_task_is_open_and_emits_event() -> None:
    activity, event = Activity.log_task(
        subject=_subject(),
        owner_user_id=uuid4(),
        title="  Call   back ",
        due_date=date(2026, 11, 5),
    )
    assert activity.kind.value == "task"
    assert activity.title == "Call back"
    assert activity.task_status is not None and activity.task_status.value == "open"
    assert isinstance(event, ActivityLogged)
    assert event.payload()["kind"] == "task"


def test_task_requires_title() -> None:
    with pytest.raises(DomainValidationError):
        Activity.log_task(subject=_subject(), owner_user_id=uuid4(), title="   ")


def test_log_call_validates_duration() -> None:
    activity, _ = Activity.log_call(
        subject=_subject(),
        owner_user_id=uuid4(),
        direction=CallDirection.OUTBOUND,
        occurred_at=datetime.now(UTC),
        duration_seconds=120,
        outcome="left voicemail",
    )
    assert activity.kind.value == "call"
    assert activity.duration_seconds == 120
    with pytest.raises(DomainValidationError):
        Activity.log_call(
            subject=_subject(),
            owner_user_id=uuid4(),
            direction=CallDirection.INBOUND,
            occurred_at=datetime.now(UTC),
            duration_seconds=-1,
        )


def test_log_note_requires_body() -> None:
    activity, _ = Activity.log_note(subject=_subject(), owner_user_id=uuid4(), body="Budget ok")
    assert activity.kind.value == "note" and activity.body == "Budget ok"
    with pytest.raises(DomainValidationError):
        Activity.log_note(subject=_subject(), owner_user_id=uuid4(), body="  ")


def test_task_complete_and_reopen() -> None:
    activity, _ = Activity.log_task(subject=_subject(), owner_user_id=uuid4(), title="Do it")
    event = activity.complete()
    assert isinstance(event, TaskCompleted)
    assert activity.task_status is not None and activity.task_status.value == "completed"
    assert activity.completed_at is not None
    with pytest.raises(ActivityStateError):
        activity.complete()  # already completed
    activity.reopen()
    assert activity.task_status is not None and activity.task_status.value == "open"
    assert activity.completed_at is None


def test_only_tasks_can_be_completed() -> None:
    note, _ = Activity.log_note(subject=_subject(), owner_user_id=uuid4(), body="note")
    with pytest.raises(ActivityStateError):
        note.complete()
    with pytest.raises(ActivityStateError):
        note.reopen()


def test_edit_rules_per_kind() -> None:
    task, _ = Activity.log_task(subject=_subject(), owner_user_id=uuid4(), title="T")
    assert task.edit(title="Renamed", due_date=date(2026, 12, 1), set_due_date=True) is True
    assert task.title == "Renamed"
    set_date = task.due_date  # local, so mypy does not narrow the attribute across edits
    assert set_date is not None and set_date.isoformat() == "2026-12-01"
    assert task.edit(due_date=None, set_due_date=True) is True
    cleared_date = task.due_date
    assert cleared_date is None
    with pytest.raises(DomainValidationError):
        task.edit(title="   ")  # a task title cannot be cleared

    note, _ = Activity.log_note(subject=_subject(), owner_user_id=uuid4(), body="body")
    with pytest.raises(DomainValidationError):
        note.edit(body="   ")  # a note body cannot be cleared
    assert note.edit(due_date=date(2026, 1, 1), set_due_date=True) is False  # ignored for notes
