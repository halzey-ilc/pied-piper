"""Activity aggregate: a task, call or note attached to a customer or a deal.

Pure Python: no Django, no ORM. Kind-specific invariants are enforced in the
factory methods, so an Activity is always internally consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from shared.domain.errors import ConflictError, DomainValidationError
from shared.domain.events import DomainEvent

MAX_TITLE_LENGTH = 200
MAX_BODY_LENGTH = 5000
MAX_OUTCOME_LENGTH = 200
MAX_DURATION_SECONDS = 24 * 60 * 60  # a single call cannot exceed a day


class ActivityKind(StrEnum):
    TASK = "task"
    CALL = "call"
    NOTE = "note"


class SubjectType(StrEnum):
    CUSTOMER = "customer"
    DEAL = "deal"


class TaskStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"


class CallDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ActivityStateError(ConflictError):
    code = "activity_invalid_state"


def _clean(text: str, *, label: str, max_length: int, required: bool) -> str | None:
    cleaned = " ".join(text.split()) if text else ""
    if not cleaned:
        if required:
            raise DomainValidationError(f"{label} is required.")
        return None
    if len(cleaned) > max_length:
        raise DomainValidationError(f"{label} must be at most {max_length} characters.")
    return cleaned


@dataclass(frozen=True, slots=True)
class SubjectRef:
    type: SubjectType
    id: UUID


@dataclass(frozen=True, kw_only=True)
class ActivityLogged(DomainEvent):
    event_type: ClassVar[str] = "activities.activity_logged"
    activity_id: UUID
    kind: str
    subject_type: str
    subject_id: UUID
    owner_user_id: UUID

    def payload(self) -> dict[str, Any]:
        return {
            "activity_id": str(self.activity_id),
            "kind": self.kind,
            "subject_type": self.subject_type,
            "subject_id": str(self.subject_id),
            "owner_user_id": str(self.owner_user_id),
        }


@dataclass(frozen=True, kw_only=True)
class TaskCompleted(DomainEvent):
    event_type: ClassVar[str] = "activities.task_completed"
    activity_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"activity_id": str(self.activity_id)}


@dataclass(slots=True)
class Activity:
    id: UUID
    kind: ActivityKind
    subject: SubjectRef
    owner_user_id: UUID
    title: str | None = None
    body: str | None = None
    team_id: UUID | None = None
    # task
    due_date: date | None = None
    task_status: TaskStatus | None = None
    completed_at: datetime | None = None
    # call
    direction: CallDirection | None = None
    duration_seconds: int | None = None
    occurred_at: datetime | None = None
    outcome: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 1

    # --- factories ----------------------------------------------------------

    @classmethod
    def log_task(
        cls,
        *,
        subject: SubjectRef,
        owner_user_id: UUID,
        title: str,
        body: str | None = None,
        due_date: date | None = None,
        team_id: UUID | None = None,
    ) -> tuple[Activity, ActivityLogged]:
        activity = cls(
            id=uuid4(),
            kind=ActivityKind.TASK,
            subject=subject,
            owner_user_id=owner_user_id,
            title=_clean(title, label="Task title", max_length=MAX_TITLE_LENGTH, required=True),
            body=_clean(body or "", label="Body", max_length=MAX_BODY_LENGTH, required=False),
            due_date=due_date,
            task_status=TaskStatus.OPEN,
            team_id=team_id,
        )
        return activity, activity._logged()

    @classmethod
    def log_call(
        cls,
        *,
        subject: SubjectRef,
        owner_user_id: UUID,
        direction: CallDirection,
        occurred_at: datetime,
        duration_seconds: int = 0,
        outcome: str | None = None,
        body: str | None = None,
        team_id: UUID | None = None,
    ) -> tuple[Activity, ActivityLogged]:
        if not 0 <= duration_seconds <= MAX_DURATION_SECONDS:
            raise DomainValidationError("Call duration is out of range.")
        activity = cls(
            id=uuid4(),
            kind=ActivityKind.CALL,
            subject=subject,
            owner_user_id=owner_user_id,
            direction=direction,
            occurred_at=occurred_at,
            duration_seconds=duration_seconds,
            outcome=_clean(
                outcome or "", label="Outcome", max_length=MAX_OUTCOME_LENGTH, required=False
            ),
            body=_clean(body or "", label="Body", max_length=MAX_BODY_LENGTH, required=False),
            team_id=team_id,
        )
        return activity, activity._logged()

    @classmethod
    def log_note(
        cls,
        *,
        subject: SubjectRef,
        owner_user_id: UUID,
        body: str,
        title: str | None = None,
        team_id: UUID | None = None,
    ) -> tuple[Activity, ActivityLogged]:
        activity = cls(
            id=uuid4(),
            kind=ActivityKind.NOTE,
            subject=subject,
            owner_user_id=owner_user_id,
            title=_clean(title or "", label="Title", max_length=MAX_TITLE_LENGTH, required=False),
            body=_clean(body, label="Note body", max_length=MAX_BODY_LENGTH, required=True),
            team_id=team_id,
        )
        return activity, activity._logged()

    def _logged(self) -> ActivityLogged:
        return ActivityLogged(
            activity_id=self.id,
            kind=self.kind.value,
            subject_type=self.subject.type.value,
            subject_id=self.subject.id,
            owner_user_id=self.owner_user_id,
        )

    # --- behaviour ----------------------------------------------------------

    def edit(
        self,
        *,
        title: str | None = None,
        body: str | None = None,
        due_date: date | None = None,
        set_due_date: bool = False,
    ) -> bool:
        """Edit free-text fields and (for tasks) the due date. Returns True if changed."""
        changed = False
        if title is not None:
            new_title = _clean(title, label="Title", max_length=MAX_TITLE_LENGTH, required=False)
            if self.kind is ActivityKind.TASK and new_title is None:
                raise DomainValidationError("Task title is required.")
            if new_title != self.title:
                self.title, changed = new_title, True
        if body is not None:
            new_body = _clean(body, label="Body", max_length=MAX_BODY_LENGTH, required=False)
            if self.kind is ActivityKind.NOTE and new_body is None:
                raise DomainValidationError("Note body is required.")
            if new_body != self.body:
                self.body, changed = new_body, True
        if set_due_date and self.kind is ActivityKind.TASK and due_date != self.due_date:
            self.due_date, changed = due_date, True
        if changed:
            self.version += 1
        return changed

    def complete(self) -> TaskCompleted:
        self._require_task("completed")
        if self.task_status is TaskStatus.COMPLETED:
            raise ActivityStateError("The task is already completed.")
        self.task_status = TaskStatus.COMPLETED
        self.completed_at = datetime.now(UTC)
        self.version += 1
        return TaskCompleted(activity_id=self.id)

    def reopen(self) -> None:
        self._require_task("reopened")
        if self.task_status is TaskStatus.OPEN:
            raise ActivityStateError("The task is already open.")
        self.task_status = TaskStatus.OPEN
        self.completed_at = None
        self.version += 1

    def _require_task(self, action: str) -> None:
        if self.kind is not ActivityKind.TASK:
            raise ActivityStateError(f"Only a task can be {action}.")
