"""Wires the website-form use cases. Keys live on the control plane; the tenant parts
(audit of key changes, lead creation) bind to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.web_forms.application.service import WebFormKeyService, WebsiteLeadIntake
from modules.web_forms.infrastructure.intake import ServiceLeadIntake
from modules.web_forms.infrastructure.store import (
    CacheDuplicateGuard,
    DjangoDepartments,
    DjangoIntakeOwners,
    DjangoKeyStore,
)
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def key_store() -> DjangoKeyStore:
    return DjangoKeyStore()


def web_form_key_service() -> WebFormKeyService:
    alias = require_tenant().db_alias  # fails closed without a tenant context
    return WebFormKeyService(
        store=DjangoKeyStore(),
        departments=DjangoDepartments(),
        audit=DjangoAuditLog(alias),
        uow=DjangoUnitOfWork(alias),
    )


def website_lead_intake() -> WebsiteLeadIntake:
    require_tenant()
    return WebsiteLeadIntake(
        store=DjangoKeyStore(),
        owners=DjangoIntakeOwners(),
        intake=ServiceLeadIntake(),
        duplicates=CacheDuplicateGuard(),
    )
