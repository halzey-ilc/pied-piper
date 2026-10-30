"""Wires the pipeline/deal use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.sales_pipeline.application.deal_service import DealService
from modules.sales_pipeline.application.loss_reason_service import LossReasonService
from modules.sales_pipeline.application.pipeline_service import PipelineService
from modules.sales_pipeline.infrastructure.customer_lookup import DjangoCustomerLookup
from modules.sales_pipeline.infrastructure.repository import (
    DjangoDealRepository,
    DjangoLossReasonRepository,
    DjangoPipelineRepository,
    DjangoStageChangeRepository,
)
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def pipeline_service() -> PipelineService:
    alias = require_tenant().db_alias
    return PipelineService(
        repository=DjangoPipelineRepository(alias),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )


def deal_service() -> DealService:
    alias = require_tenant().db_alias
    return DealService(
        deals=DjangoDealRepository(alias),
        pipelines=DjangoPipelineRepository(alias),
        customers=DjangoCustomerLookup(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
        loss_reasons=DjangoLossReasonRepository(alias),
        stage_changes=DjangoStageChangeRepository(alias),
    )


def loss_reason_service() -> LossReasonService:
    alias = require_tenant().db_alias
    return LossReasonService(
        repository=DjangoLossReasonRepository(alias),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )
