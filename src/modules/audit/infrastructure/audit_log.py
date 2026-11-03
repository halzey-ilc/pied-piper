from __future__ import annotations

from modules.audit.domain.entry import AuditEntry
from modules.audit.infrastructure.models import AuditRecord
from platform_core.application.context import current_correlation_id, current_request_id


class DjangoAuditLog:
    def __init__(self, using: str) -> None:
        self.using = using

    def record(self, entry: AuditEntry) -> None:
        AuditRecord.objects.using(self.using).create(
            occurred_at=entry.occurred_at,
            actor_user_id=entry.actor_user_id,
            action=entry.action,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            changed_fields=list(entry.changed_fields),
            details={key: str(value) for key, value in entry.details.items()},
            request_id=current_request_id() or "",
            correlation_id=current_correlation_id() or "",
        )

    def entries_for(
        self, resource_type: str, resource_id: str, *, limit: int = 100
    ) -> list[AuditEntry]:
        rows = (
            AuditRecord.objects.using(self.using)
            .filter(resource_type=resource_type, resource_id=resource_id)
            .order_by("-occurred_at")[:limit]
        )
        return [
            AuditEntry(
                actor_user_id=row.actor_user_id,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                changed_fields=tuple(row.changed_fields or ()),
                occurred_at=row.occurred_at,
                details=dict(row.details or {}),
            )
            for row in rows
        ]
