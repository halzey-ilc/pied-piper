"""Pipeline system stages in API tests: the next step a move to an open stage needs, and
closing a deal as a sale the way a confirmed payment does (POST /win/ is refused)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.sales_pipeline.application.sale_closing import SaleClosing
from modules.sales_pipeline.infrastructure.repository import (
    DjangoDealRepository,
    DjangoPipelineRepository,
    DjangoStageChangeRepository,
)
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from platform_core.infrastructure.tenancy.services import system_tenant_scope
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def next_step(title: str = "Перезвонить клиенту", due_date: str = "2030-01-10") -> dict[str, Any]:
    return {"title": title, "due_date": due_date}


def close_as_sale(deal_id: str, tenant: str = "acme") -> dict[str, Any]:
    """What the payments context does once a payment is confirmed (``SaleClosing``): the
    deal moves to «Продажа» and is won (stage history, audit and the deal_won event)."""
    with system_tenant_scope(tenant) as context:
        alias = context.db_alias
        deals = DjangoDealRepository(alias)
        deal = deals.get(UUID(deal_id))
        assert deal is not None
        SaleClosing(
            deals=deals,
            pipelines=DjangoPipelineRepository(alias),
            stage_changes=DjangoStageChangeRepository(alias),
            uow=DjangoUnitOfWork(alias),
            outbox=DjangoEventOutbox(alias),
            audit=DjangoAuditLog(alias),
        ).close_deal_as_sale(deal.id, deal.owner_user_id)
        closed = deals.get(deal.id)
        assert closed is not None
    return {"status": closed.status.value, "stage_id": str(closed.stage_id)}
