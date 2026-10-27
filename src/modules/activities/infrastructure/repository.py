from __future__ import annotations

from uuid import UUID

from django.db.models import F, Q, QuerySet

from modules.activities.application.ports import ActivityFilter, ActivityVisibility
from modules.activities.domain.activity import (
    Activity,
    ActivityKind,
    CallDirection,
    SubjectRef,
    SubjectType,
    TaskStatus,
)
from modules.activities.infrastructure.models import ActivityRecord
from shared.domain.errors import ConflictError


def _to_domain(row: ActivityRecord) -> Activity:
    return Activity(
        id=row.id,
        kind=ActivityKind(row.kind),
        subject=SubjectRef(type=SubjectType(row.subject_type), id=row.subject_id),
        owner_user_id=row.owner_user_id,
        title=row.title,
        body=row.body,
        team_id=row.team_id,
        due_date=row.due_date,
        task_status=TaskStatus(row.task_status) if row.task_status else None,
        completed_at=row.completed_at,
        direction=CallDirection(row.direction) if row.direction else None,
        duration_seconds=row.duration_seconds,
        occurred_at=row.occurred_at,
        outcome=row.outcome,
        created_at=row.created_at,
        version=row.version,
    )


class DjangoActivityRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[ActivityRecord]:
        return ActivityRecord.objects.using(self.using)

    def _filtered(
        self, visibility: ActivityVisibility, filters: ActivityFilter
    ) -> QuerySet[ActivityRecord]:
        qs = self._qs()
        if visibility.owner_user_id is not None:
            condition = Q(owner_user_id=visibility.owner_user_id)
            if visibility.team_ids:
                condition |= Q(team_id__in=visibility.team_ids)
            qs = qs.filter(condition)
        if filters.kind is not None:
            qs = qs.filter(kind=filters.kind.value)
        if filters.subject is not None:
            qs = qs.filter(subject_type=filters.subject.type.value, subject_id=filters.subject.id)
        if filters.task_status is not None:
            qs = qs.filter(task_status=filters.task_status.value)
        return qs

    def add(self, activity: Activity) -> None:
        self._qs().create(
            id=activity.id,
            kind=activity.kind.value,
            subject_type=activity.subject.type.value,
            subject_id=activity.subject.id,
            owner_user_id=activity.owner_user_id,
            team_id=activity.team_id,
            title=activity.title,
            body=activity.body,
            due_date=activity.due_date,
            task_status=activity.task_status.value if activity.task_status else None,
            completed_at=activity.completed_at,
            direction=activity.direction.value if activity.direction else None,
            duration_seconds=activity.duration_seconds,
            occurred_at=activity.occurred_at,
            outcome=activity.outcome,
            version=activity.version,
            created_at=activity.created_at,
        )

    def get(self, activity_id: UUID) -> Activity | None:
        row = self._qs().filter(id=activity_id).first()
        return None if row is None else _to_domain(row)

    def save(self, activity: Activity, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=activity.id, version=expected_version)
            .update(
                title=activity.title,
                body=activity.body,
                due_date=activity.due_date,
                task_status=activity.task_status.value if activity.task_status else None,
                completed_at=activity.completed_at,
                outcome=activity.outcome,
                version=F("version") + 1,
            )
        )
        if updated != 1:
            raise ConflictError("Activity was modified by someone else; reload and retry.")

    def list_visible(
        self, visibility: ActivityVisibility, filters: ActivityFilter, *, limit: int, offset: int
    ) -> list[Activity]:
        rows = self._filtered(visibility, filters).order_by("-created_at", "id")[
            offset : offset + limit
        ]
        return [_to_domain(row) for row in rows]

    def count(self, visibility: ActivityVisibility, filters: ActivityFilter) -> int:
        return self._filtered(visibility, filters).count()
