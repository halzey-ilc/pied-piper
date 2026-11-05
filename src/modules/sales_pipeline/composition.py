"""Wires the pipeline/deal use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from uuid import UUID

from modules.activities.infrastructure.next_steps import ActivityNextSteps
from modules.audit.domain.entry import AuditEntry
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.organizations.infrastructure.memberships import DjangoMemberDirectory
from modules.organizations.infrastructure.models import Membership
from modules.sales_pipeline.application.custom_field_service import CustomFieldService
from modules.sales_pipeline.application.deal_service import DealService
from modules.sales_pipeline.application.loss_reason_service import LossReasonService
from modules.sales_pipeline.application.pipeline_service import PipelineService
from modules.sales_pipeline.application.product_service import ProductService
from modules.sales_pipeline.domain.pipeline import Pipeline, StageDefinition, StageKind
from modules.sales_pipeline.infrastructure.customer_lookup import DjangoCustomerLookup
from modules.sales_pipeline.infrastructure.deal_meetings import MeetingsDealMeetings
from modules.sales_pipeline.infrastructure.repository import (
    DjangoCustomFieldRepository,
    DjangoDealRepository,
    DjangoLossReasonRepository,
    DjangoPipelineRepository,
    DjangoProductRepository,
    DjangoStageChangeRepository,
)
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


class _DepartmentRoster:
    """Active members of one company's sales department (control plane)."""

    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id

    def member_ids(self, department_id: UUID) -> set[UUID]:
        return set(
            Membership.objects.using("default")
            .filter(
                organization_id=self.tenant_id,
                department_id=department_id,
                is_active=True,
                user__is_active=True,
            )
            .values_list("user_id", flat=True)
        )


def pipeline_service() -> PipelineService:
    tenant = require_tenant()
    alias = tenant.db_alias
    return PipelineService(
        repository=DjangoPipelineRepository(alias),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
        deals=DjangoDealRepository(alias),
        stage_changes=DjangoStageChangeRepository(alias),
        roster=_DepartmentRoster(tenant.tenant_id),
    )


def deal_service() -> DealService:
    tenant = require_tenant()
    alias = tenant.db_alias
    return DealService(
        deals=DjangoDealRepository(alias),
        pipelines=DjangoPipelineRepository(alias),
        customers=DjangoCustomerLookup(alias),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
        loss_reasons=DjangoLossReasonRepository(alias),
        stage_changes=DjangoStageChangeRepository(alias),
        custom_fields=DjangoCustomFieldRepository(alias),
        members=DjangoMemberDirectory(tenant.tenant_id),
        trail=DjangoAuditLog(alias),
        meetings=MeetingsDealMeetings(),
        next_steps=ActivityNextSteps(),
        products=DjangoProductRepository(alias),
    )


def loss_reason_service() -> LossReasonService:
    alias = require_tenant().db_alias
    return LossReasonService(
        repository=DjangoLossReasonRepository(alias),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )


def product_service() -> ProductService:
    alias = require_tenant().db_alias
    return ProductService(
        repository=DjangoProductRepository(alias),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )


def custom_field_service() -> CustomFieldService:
    alias = require_tenant().db_alias
    return CustomFieldService(
        repository=DjangoCustomFieldRepository(alias),
        uow=DjangoUnitOfWork(alias),
        audit=DjangoAuditLog(alias),
    )


# The pipeline a newly connected company starts with; the director can change it later.
_STARTER_STAGES = (
    ("Новый", 10, "open"),
    ("Квалификация", 30, "open"),
    ("Предложение", 60, "open"),
    ("Выиграна", 100, "won"),
    ("Проиграна", 0, "lost"),
)


def ensure_starter_pipeline(alias: str) -> bool:
    """Give a company without pipelines the standard one (platform onboarding). Idempotent."""
    repository = DjangoPipelineRepository(alias)
    if repository.list_all():
        return False
    repository.add(
        Pipeline.create(
            name="Основная воронка",
            stages=[StageDefinition(n, p, StageKind(k)) for n, p, k in _STARTER_STAGES],
            is_default=True,
        )
    )
    return True


def hand_over_open_deals(from_user: UUID, to_user: UUID, to_team: UUID | None, actor: UUID) -> int:
    """Staff offboarding (called by organizations after the decision): the open deals of a
    deactivated member go to ``to_user``; each move is audited as ``deal.reassigned``."""
    alias = require_tenant().db_alias
    with DjangoUnitOfWork(alias):
        moved = DjangoDealRepository(alias).hand_over_open_deals(from_user, to_user, to_team)
        audit = DjangoAuditLog(alias)
        for deal_id in moved:
            audit.record(
                AuditEntry(
                    actor_user_id=actor,
                    action="deal.reassigned",
                    resource_type="deal",
                    resource_id=str(deal_id),
                    changed_fields=("owner",),
                )
            )
    return len(moved)
