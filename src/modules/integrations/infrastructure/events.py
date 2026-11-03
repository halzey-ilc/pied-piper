"""Inbound webhook events of the active company, read from its inbox (read-only)."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import DatabaseError

from modules.integrations.application.status import InboundError, InboundStats
from modules.integrations.domain.providers import Provider
from platform_core.infrastructure.messaging.models import InboxMessage, InboxStatus

_RECENT_ERRORS = 20


class DjangoInboundEvents:
    def __init__(self, using: str) -> None:
        self.using = using

    def stats(self, provider: Provider, now: datetime) -> InboundStats:
        rows = InboxMessage.objects.using(self.using).filter(source=f"webhook.{provider.value}")
        last = rows.order_by("-received_at").values_list("received_at", flat=True).first()
        errors = rows.exclude(last_error="").order_by("-received_at")[:_RECENT_ERRORS]
        return InboundStats(
            received_total=rows.count(),
            received_last_24h=rows.filter(received_at__gte=now - timedelta(hours=24)).count(),
            failed_total=rows.filter(status=InboxStatus.FAILED).count(),
            last_received_at=last,
            recent_errors=[
                InboundError(received_at=row.received_at, status=row.status, error=row.last_error)
                for row in errors
            ],
        )

    def reachable(self) -> bool:
        try:
            InboxMessage.objects.using(self.using).exists()
        except DatabaseError:
            return False
        return True
