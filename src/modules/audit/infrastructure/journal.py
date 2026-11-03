from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from django.db.models import QuerySet

from modules.audit.application.journal import JournalEntry, JournalFilter
from modules.audit.infrastructure.models import AuditRecord
from modules.organizations.infrastructure.models import Membership


class DjangoAuditJournal:
    """Reads the company's audit trail (the tenant database), newest first."""

    def __init__(self, using: str) -> None:
        self.using = using

    def list(
        self,
        filters: JournalFilter,
        *,
        actor_ids: frozenset[UUID] | None,
        limit: int,
        offset: int,
    ) -> tuple[list[JournalEntry], int]:
        qs = self._filtered(filters)
        if actor_ids is not None:
            qs = qs.filter(actor_user_id__in=list(actor_ids))
        total = qs.count()
        rows = qs.order_by("-occurred_at", "-id")[offset : offset + limit]
        return [_entry(row) for row in rows], total

    def _filtered(self, f: JournalFilter) -> QuerySet[AuditRecord]:
        qs = AuditRecord.objects.using(self.using).all()
        if f.actor_user_id is not None:
            qs = qs.filter(actor_user_id=f.actor_user_id)
        if f.resource_type:
            qs = qs.filter(resource_type=f.resource_type)
        if f.resource_id:
            qs = qs.filter(resource_id=f.resource_id)
        if f.action:
            qs = (
                qs.filter(action__startswith=f.action)
                if f.action.endswith(".")
                else qs.filter(action=f.action)
            )
        # Day bounds in UTC: the journal is an operational log, not a business report.
        if f.date_from is not None:
            qs = qs.filter(occurred_at__gte=datetime.combine(f.date_from, time.min, tzinfo=UTC))
        if f.date_to is not None:
            end = datetime.combine(f.date_to + timedelta(days=1), time.min, tzinfo=UTC)
            qs = qs.filter(occurred_at__lt=end)
        return qs


def _entry(row: AuditRecord) -> JournalEntry:
    details = getattr(row, "details", None) or {}
    return JournalEntry(
        id=row.id,
        occurred_at=row.occurred_at,
        actor_user_id=row.actor_user_id,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        changed_fields=tuple(row.changed_fields or ()),
        details={str(k): str(v) for k, v in details.items()},
    )


def department_members(tenant_id: UUID, department_id: UUID) -> frozenset[UUID]:
    """Every user who is or was a member of this department (history stays visible)."""
    return frozenset(
        Membership.objects.using("default")
        .filter(organization_id=tenant_id, department_id=department_id)
        .values_list("user_id", flat=True)
    )
