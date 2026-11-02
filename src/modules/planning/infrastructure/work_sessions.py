"""WorkSessionRepository adapter (tenant database)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import IntegrityError, transaction

from modules.planning.application.work_sessions import WorkSessionFilter
from modules.planning.domain.work_session import (
    MAX_OPEN,
    WorkSession,
    WorkSessionStateError,
    WorkSessionStatus,
)
from modules.planning.infrastructure.models import WorkSessionRecord


def _domain(row: WorkSessionRecord) -> WorkSession:
    return WorkSession(
        id=row.id,
        user_id=row.user_id,
        team_id=row.team_id,
        started_at=row.started_at,
        status=WorkSessionStatus(row.status),
        ended_at=row.ended_at,
        settled_at=row.settled_at,
        created_at=row.created_at,
    )


class DjangoWorkSessionRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def add(self, session: WorkSession) -> None:
        try:
            with transaction.atomic(using=self.using):
                WorkSessionRecord.objects.using(self.using).create(
                    id=session.id,
                    user_id=session.user_id,
                    team_id=session.team_id,
                    started_at=session.started_at,
                    status=session.status.value,
                    created_at=session.created_at,
                )
        except IntegrityError:
            raise WorkSessionStateError("A working session is already open.") from None

    def open_for_update(self, user_id: UUID) -> WorkSession | None:
        row = (
            WorkSessionRecord.objects.using(self.using)
            .select_for_update()
            .filter(user_id=user_id, status=WorkSessionStatus.OPEN.value)
            .first()
        )
        return None if row is None else _domain(row)

    def settle(self, session: WorkSession) -> None:
        updated = (
            WorkSessionRecord.objects.using(self.using)
            .filter(id=session.id, status=WorkSessionStatus.OPEN.value)
            .update(
                status=session.status.value,
                ended_at=session.ended_at,
                settled_at=session.settled_at,
            )
        )
        if updated != 1:
            raise WorkSessionStateError("The session changed meanwhile; reload.")

    def list(
        self, filters: WorkSessionFilter, *, limit: int, offset: int
    ) -> tuple[list[WorkSession], int]:
        qs = WorkSessionRecord.objects.using(self.using).order_by("-started_at", "id")
        if filters.user_ids is not None:
            qs = qs.filter(user_id__in=filters.user_ids)
        if filters.status is not None:
            qs = qs.filter(status=filters.status.value)
        if filters.started_from is not None:
            qs = qs.filter(started_at__gte=filters.started_from)
        if filters.started_before is not None:
            qs = qs.filter(started_at__lt=filters.started_before)
        return [_domain(r) for r in qs[offset : offset + limit]], qs.count()

    def mark_stale_unconfirmed(self, now: datetime) -> int:
        """Bulk policy for sessions nobody stopped (see domain.work_session)."""
        return (
            WorkSessionRecord.objects.using(self.using)
            .filter(status=WorkSessionStatus.OPEN.value, started_at__lt=now - MAX_OPEN)
            .update(status=WorkSessionStatus.UNCONFIRMED.value, ended_at=None, settled_at=now)
        )
