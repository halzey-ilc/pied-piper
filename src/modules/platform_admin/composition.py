"""Wires platform administration to the control-plane (default) database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.organizations.composition import temporary_password
from modules.platform_admin.application.ports import TenantProvisioner
from modules.platform_admin.application.service import PlatformAdminService, SupportService
from modules.platform_admin.infrastructure.store import (
    DjangoErrorLog,
    DjangoPlatformStore,
    DjangoTenantProvisioner,
    DjangoTicketStore,
)
from modules.subscriptions.infrastructure.repository import DjangoSubscriptionRepository
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork

_CONTROL_PLANE = "default"


def tenant_provisioner() -> TenantProvisioner:
    return DjangoTenantProvisioner()


def platform_admin_service() -> PlatformAdminService:
    return PlatformAdminService(
        store=DjangoPlatformStore(),
        subscriptions=DjangoSubscriptionRepository(),
        provisioner=tenant_provisioner(),
        errors=DjangoErrorLog(),
        tickets=DjangoTicketStore(),
        uow=DjangoUnitOfWork(_CONTROL_PLANE),
        outbox=DjangoEventOutbox(_CONTROL_PLANE),
        audit=DjangoAuditLog(_CONTROL_PLANE),
        passwords=temporary_password,
    )


def support_service() -> SupportService:
    return SupportService(DjangoTicketStore())
