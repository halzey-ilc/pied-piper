"""Wires the subscription use cases to the control-plane (default) database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.subscriptions.application.service import SubscriptionService
from modules.subscriptions.application.usage import PlanUsageService
from modules.subscriptions.infrastructure.repository import DjangoSubscriptionRepository
from modules.subscriptions.infrastructure.usage import count_usage
from platform_core.application.context import require_tenant
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


def plan_usage_service() -> PlanUsageService:
    # Customers and deals are counted in the current company's own database.
    alias = require_tenant().db_alias
    return PlanUsageService(
        repository=DjangoSubscriptionRepository(),
        counter=lambda tenant_id: count_usage(tenant_id, alias),
    )
