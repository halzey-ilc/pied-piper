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
)
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, NotFoundError
from shared.domain.events import DomainEvent

AGGREGATE = "activity"
_Factory = Callable[[], tuple["Activity", "DomainEvent"]]


@dataclass(frozen=True, slots=True)
class LogTask:
    subject: SubjectRef
    title: str
    body: str | None = None
    due_date: date | None = None


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
        return self._log(
            principal,
            command.subject,
            lambda: Activity.log_task(
                subject=command.subject,
                owner_user_id=principal.user_id,
                team_id=principal.department_id,
                title=command.title,
                body=command.body,
                due_date=command.due_date,
            ),
        )

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
            ):
                self._repo.save(activity, expected_version=version)
                self._record(principal, "activity.updated", activity)
        return activity

    def complete_task(self, principal: Principal, activity_id: UUID) -> Activity:
        with self._uow:
            activity = self._load_for_write(principal, activity_id)
            version = activity.version
            event = activity.complete()
            self._repo.save(activity, expected_version=version)
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(activity.id))
            self._record(principal, "activity.task_completed", activity)
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
