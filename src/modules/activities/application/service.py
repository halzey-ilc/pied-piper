"""Activity use cases. Authorization (RBAC + ownership scope) is enforced here for
every operation, independently of the HTTP layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
    Scope,
)
from modules.activities.application.ports import (
    ActivityFilter,
    ActivityRepository,
    ActivityVisibility,
    SubjectChecker,
)
from modules.activities.domain.activity import (
    Activity,
    CallDirection,
    SubjectRef,
    SubjectType,
    TaskPriority,
    TaskType,
)
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import AccessDeniedError, ConflictError, NotFoundError
from shared.domain.events import DomainEvent

AGGREGATE = "activity"
# A deal may carry at most this many open tasks at once (spec: operations hygiene).
MAX_OPEN_TASKS_PER_DEAL = 2
_Factory = Callable[[], tuple["Activity", "DomainEvent"]]


@dataclass(frozen=True, slots=True)
class LogTask:
    subject: SubjectRef
    title: str
    body: str | None = None
    due_date: date | None = None
    task_type: TaskType = TaskType.OTHER
    priority: TaskPriority = TaskPriority.NORMAL
    assignee_user_id: UUID | None = None  # defaults to the creator


@dataclass(frozen=True, slots=True)
class LogCall:
    subject: SubjectRef
    direction: CallDirection
    occurred_at: datetime
    duration_seconds: int = 0
    outcome: str | None = None
    body: str | None = None


@dataclass(frozen=True, slots=True)
class LogNote:
    subject: SubjectRef
    body: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class EditActivity:
    activity_id: UUID
    expected_version: int | None = None
    title: str | None = None
    body: str | None = None
    due_date: date | None = None
    set_due_date: bool = False
    priority: TaskPriority | None = None


@dataclass(frozen=True, slots=True)
class ActivityPage:
    items: list[Activity]
    total: int


def _ownership(activity: Activity) -> ResourceOwnership:
    return ResourceOwnership(owner_user_id=activity.owner_user_id, team_id=activity.team_id)


class ActivityService:
    def __init__(
        self,
        repository: ActivityRepository,
        subjects: SubjectChecker,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._subjects = subjects
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._policy = policy or AccessPolicy()

    # --- reads --------------------------------------------------------------

    def get(self, principal: Principal, activity_id: UUID) -> Activity:
        activity = self._repo.get(activity_id)
        if activity is None or not self._policy.allows(
            principal, Permission.ACTIVITIES_VIEW, _ownership(activity)
        ):
            raise NotFoundError("Activity not found.")
        return activity

    def timeline(
        self, principal: Principal, filters: ActivityFilter, *, limit: int, offset: int
    ) -> ActivityPage:
        scope = self._policy.require(principal, Permission.ACTIVITIES_VIEW)
        visibility = self._visibility(principal, scope)
        return ActivityPage(
            items=self._repo.list_visible(visibility, filters, limit=limit, offset=offset),
            total=self._repo.count(visibility, filters),
        )

    def _visibility(self, principal: Principal, scope: Scope) -> ActivityVisibility:
        if scope is Scope.ALL:
            return ActivityVisibility()
        if scope is Scope.TEAM:
            return ActivityVisibility(owner_user_id=principal.user_id, team_ids=principal.team_ids)
        return ActivityVisibility(owner_user_id=principal.user_id)

    # --- writes -------------------------------------------------------------

    def log_task(self, principal: Principal, command: LogTask) -> Activity:
        scope = self._policy.require(principal, Permission.ACTIVITIES_CREATE)
        assignee = command.assignee_user_id or principal.user_id
        # A manager (OWN scope) may only assign tasks to themselves; a head of
        # sales (TEAM) or director/admin (ALL) may assign them to others.
        if assignee != principal.user_id and scope is Scope.OWN:
            raise AccessDeniedError("You can only assign tasks to yourself.")
        with self._uow:
            if not self._subjects.exists(command.subject.type, command.subject.id):
                raise NotFoundError(
                    f"The {command.subject.type.value} this task refers to was not found."
                )
            if (
                command.subject.type is SubjectType.DEAL
                and self._repo.count_open_tasks_for_subject(command.subject)
                >= MAX_OPEN_TASKS_PER_DEAL
            ):
                raise ConflictError(
                    f"A deal can have at most {MAX_OPEN_TASKS_PER_DEAL} open tasks."
                )
            activity, event = Activity.log_task(
                subject=command.subject,
                created_by=principal.user_id,
                assignee_user_id=assignee,
                team_id=principal.department_id,
                title=command.title,
                body=command.body,
                due_date=command.due_date,
                task_type=command.task_type,
                priority=command.priority,
            )
            self._repo.add(activity)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(activity.id))
            self._record(principal, "activity.logged", activity)
        return activity

    def log_call(self, principal: Principal, command: LogCall) -> Activity:
        return self._log(
            principal,
            command.subject,
            lambda: Activity.log_call(
                subject=command.subject,
                owner_user_id=principal.user_id,
                team_id=principal.department_id,
                direction=command.direction,
                occurred_at=command.occurred_at,
                duration_seconds=command.duration_seconds,
                outcome=command.outcome,
                body=command.body,
            ),
        )

    def log_note(self, principal: Principal, command: LogNote) -> Activity:
        return self._log(
            principal,
            command.subject,
            lambda: Activity.log_note(
                subject=command.subject,
                owner_user_id=principal.user_id,
                team_id=principal.department_id,
                body=command.body,
                title=command.title,
            ),
        )

    def edit(self, principal: Principal, command: EditActivity) -> Activity:
        with self._uow:
            activity = self._load_for_write(principal, command.activity_id)
            self._check_version(activity, command.expected_version)
            version = activity.version
            if activity.edit(
                title=command.title,
                body=command.body,
                due_date=command.due_date,
                set_due_date=command.set_due_date,
                priority=command.priority,
            ):
                self._repo.save(activity, expected_version=version)
                self._record(principal, "activity.updated", activity)
        return activity

    def complete_task(
        self, principal: Principal, activity_id: UUID, result: str | None = None
    ) -> Activity:
        with self._uow:
            activity = self._load_for_write(principal, activity_id)
            version = activity.version
            event = activity.complete(result)
            self._repo.save(activity, expected_version=version)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(activity.id))
            self._record(principal, "activity.task_completed", activity)
        return activity

    def cancel_task(
        self, principal: Principal, activity_id: UUID, result: str | None = None
    ) -> Activity:
        with self._uow:
            activity = self._load_for_write(principal, activity_id)
            version = activity.version
            activity.cancel(result)
            self._repo.save(activity, expected_version=version)
            self._record(principal, "activity.task_cancelled", activity)
        return activity

    def reassign_task(
        self, principal: Principal, activity_id: UUID, assignee_user_id: UUID
    ) -> Activity:
        with self._uow:
            activity = self._load_for_write(principal, activity_id)
            # Reassigning to someone else needs management scope (not OWN).
            scope = self._policy.require(
                principal, Permission.ACTIVITIES_UPDATE, _ownership(activity)
            )
            if assignee_user_id != activity.assignee_user_id and scope is Scope.OWN:
                raise AccessDeniedError("You cannot reassign tasks to another user.")
            version = activity.version
            if activity.reassign(assignee_user_id, team_id=principal.department_id, set_team=True):
                self._repo.save(activity, expected_version=version)
                self._record(principal, "activity.task_reassigned", activity)
        return activity

    def reopen_task(self, principal: Principal, activity_id: UUID) -> Activity:
        with self._uow:
            activity = self._load_for_write(principal, activity_id)
            version = activity.version
            activity.reopen()
            self._repo.save(activity, expected_version=version)
            self._record(principal, "activity.task_reopened", activity)
        return activity

    # --- helpers ------------------------------------------------------------

    def _log(
        self,
        principal: Principal,
        subject: SubjectRef,
        factory: _Factory,
    ) -> Activity:
        self._policy.require(principal, Permission.ACTIVITIES_CREATE)
        with self._uow:
            if not self._subjects.exists(subject.type, subject.id):
                raise NotFoundError(
                    f"The {subject.type.value} this activity refers to was not found."
                )
            activity, event = factory()
            self._repo.add(activity)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(activity.id))
            self._record(principal, "activity.logged", activity)
        return activity

    def _load_for_write(self, principal: Principal, activity_id: UUID) -> Activity:
        activity = self.get(principal, activity_id)  # 404 if not visible
        self._policy.require(principal, Permission.ACTIVITIES_UPDATE, _ownership(activity))
        return activity

    def _check_version(self, activity: Activity, expected_version: int | None) -> None:
        if expected_version is not None and expected_version != activity.version:
            raise ConflictError("Activity was modified by someone else; reload and retry.")

    def _record(self, principal: Principal, action: str, activity: Activity) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(activity.id),
            )
        )
