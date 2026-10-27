"""Wires the subscription use cases to the control-plane (default) database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.subscriptions.application.service import SubscriptionService
from modules.subscriptions.infrastructure.repository import DjangoSubscriptionRepository
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork

_CONTROL_PLANE = "default"


def subscription_service() -> SubscriptionService:
    return SubscriptionService(
        repository=DjangoSubscriptionRepository(),
        uow=DjangoUnitOfWork(_CONTROL_PLANE),
        outbox=DjangoEventOutbox(_CONTROL_PLANE),
        audit=DjangoAuditLog(_CONTROL_PLANE),
    )
