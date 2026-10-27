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
            request_id=current_request_id() or "",
            correlation_id=current_correlation_id() or "",
        )
