"""Deal use cases. Authorization (RBAC + ownership scope) is enforced here for
every operation, independently of the HTTP layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    Permission,
    Principal,
    ResourceOwnership,
    Scope,
)
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.sales_pipeline.application.ports import (
    CustomerLookup,
    DealFilter,
    DealRepository,
    DealVisibility,
    LossReasonRepository,
    PipelineRepository,
    StageChangeRepository,
)
from modules.sales_pipeline.domain.deal import Deal, DealStatus
from modules.sales_pipeline.domain.pipeline import Pipeline, StageKind
from modules.sales_pipeline.domain.stage_change import StageChange
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, DomainValidationError, NotFoundError
from shared.domain.events import DomainEvent
from shared.domain.value_objects import Money

AGGREGATE = "deal"


@dataclass(frozen=True, slots=True)
class OpenDeal:
    title: str
    amount: Decimal
    currency: str = "USD"
    pipeline_id: UUID | None = None  # default pipeline when omitted
    customer_id: UUID | None = None
    expected_close_date: date | None = None


@dataclass(frozen=True, slots=True)
class EditDeal:
    deal_id: UUID
    expected_version: int | None = None
    title: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    customer_id: UUID | None = None
    set_customer: bool = False
    expected_close_date: date | None = None
    set_expected_close: bool = False


@dataclass(frozen=True, slots=True)
class DealPage:
    items: list[Deal]
    total: int


def _ownership(deal: Deal) -> ResourceOwnership:
    return ResourceOwnership(owner_user_id=deal.owner_user_id, team_id=deal.team_id)


class DealService:
    def __init__(
        self,
        deals: DealRepository,
        pipelines: PipelineRepository,
        customers: CustomerLookup,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        loss_reasons: LossReasonRepository | None = None,
        stage_changes: StageChangeRepository | None = None,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._deals = deals
        self._pipelines = pipelines
        self._customers = customers
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._loss_reasons = loss_reasons
        self._stage_changes = stage_changes
        self._policy = policy or AccessPolicy()

    # --- reads --------------------------------------------------------------

    def get(self, principal: Principal, deal_id: UUID) -> Deal:
        deal = self._deals.get(deal_id)
        if deal is None or not self._policy.allows(
            principal, Permission.DEALS_VIEW, _ownership(deal)
        ):
            raise NotFoundError("Deal not found.")
        return deal

    def list_deals(
        self, principal: Principal, filters: DealFilter, *, limit: int, offset: int
    ) -> DealPage:
        scope = self._policy.require(principal, Permission.DEALS_VIEW)
        visibility = self._visibility(principal, scope)
        return DealPage(
            items=self._deals.list_visible(visibility, filters, limit=limit, offset=offset),
            total=self._deals.count(visibility, filters),
        )

    def stage_history(self, principal: Principal, deal_id: UUID) -> list[StageChange]:
        self.get(principal, deal_id)  # 404 if the deal is not visible to the caller
        if self._stage_changes is None:
            return []
        return self._stage_changes.list_for_deal(deal_id)

    def _visibility(self, principal: Principal, scope: Scope) -> DealVisibility:
        if scope is Scope.ALL:
            return DealVisibility()
        if scope is Scope.TEAM:
            return DealVisibility(owner_user_id=principal.user_id, team_ids=principal.team_ids)
        return DealVisibility(owner_user_id=principal.user_id)

    # --- writes -------------------------------------------------------------

    def open_deal(self, principal: Principal, command: OpenDeal) -> Deal:
        self._policy.require(principal, Permission.DEALS_CREATE)
        with self._uow:
            pipeline = self._resolve_pipeline(command.pipeline_id)
            if command.customer_id is not None and not self._customers.exists(command.customer_id):
                raise NotFoundError("Linked customer not found.")
            deal, event = Deal.open(
                title=command.title,
                pipeline_id=pipeline.id,
                stage_id=pipeline.initial_stage.id,
                value=Money(command.amount, command.currency),
                owner_user_id=principal.user_id,
                team_id=principal.department_id,
                customer_id=command.customer_id,
                expected_close_date=command.expected_close_date,
            )
            self._deals.add(deal)
            self._emit(event, deal)
            self._record(principal, "deal.created", deal)
        return deal

    def edit(self, principal: Principal, command: EditDeal) -> Deal:
        with self._uow:
            deal = self._load_for_write(principal, command.deal_id)
            self._check_version(deal, command.expected_version)
            if (
                command.set_customer
                and command.customer_id is not None
                and not self._customers.exists(command.customer_id)
            ):
                raise NotFoundError("Linked customer not found.")
            value = self._money_update(deal, command)
            version = deal.version
            changed = deal.update_details(
                title=command.title,
                value=value,
                customer_id=command.customer_id,
                set_customer=command.set_customer,
                expected_close_date=command.expected_close_date,
                set_expected_close=command.set_expected_close,
            )
            if changed:
                self._deals.save(deal, expected_version=version)
                self._record(principal, "deal.updated", deal)
        return deal

    def move_stage(
        self,
        principal: Principal,
        deal_id: UUID,
        stage_id: UUID,
        *,
        reason: str | None = None,
        loss_reason_id: UUID | None = None,
    ) -> Deal:
        """Transition a deal to a stage. A won/lost-kind stage closes the deal
        (a lost stage needs a reason: a catalog ``loss_reason_id`` and/or free-text
        ``reason``); an open-kind stage just moves it."""
        with self._uow:
            deal = self._load_for_write(principal, deal_id)
            pipeline = self._pipelines.get(deal.pipeline_id)
            if pipeline is None:
                raise NotFoundError("Stage does not belong to the deal's pipeline.")
            stage = pipeline.stage(stage_id)  # 404 if not in this pipeline
            if not stage.is_active:
                raise DomainValidationError("That stage is no longer active.")
            version = deal.version
            from_stage = deal.stage_id
            if stage.kind is StageKind.WON:
                event: DomainEvent | None = deal.win(stage_id=stage_id)
                audit_action = "deal.won"
            elif stage.kind is StageKind.LOST:
                text = self._resolve_loss_reason(reason, loss_reason_id)
                event = deal.lose(text, stage_id=stage_id, loss_reason_id=loss_reason_id)
                audit_action = "deal.lost"
            else:
                event = deal.move_to(stage_id)
                audit_action = "deal.stage_changed"
            if event is not None:
                self._deals.save(deal, expected_version=version)
                self._record_stage_change(principal, deal, from_stage)
                self._emit(event, deal)
                self._record(principal, audit_action, deal)
        return deal

    def _resolve_loss_reason(self, reason: str | None, loss_reason_id: UUID | None) -> str:
        """Validate a catalog loss reason (if given) and return the free-text note
        to store: the explicit ``reason`` when provided, else the catalog name."""
        catalog_name: str | None = None
        if loss_reason_id is not None:
            if self._loss_reasons is None:
                raise DomainValidationError("Loss reasons are not available.")
            catalog = self._loss_reasons.get(loss_reason_id)
            if catalog is None or not catalog.is_active:
                raise DomainValidationError("Unknown or inactive loss reason.")
            catalog_name = catalog.name
        text = (reason or catalog_name or "").strip()
        if not text:
            raise DomainValidationError("A loss reason is required to lose a deal.")
        return text

    def win(self, principal: Principal, deal_id: UUID) -> Deal:
        return self._close(principal, deal_id, lambda deal: deal.win(), "deal.won")

    def lose(self, principal: Principal, deal_id: UUID, reason: str) -> Deal:
        return self._close(principal, deal_id, lambda deal: deal.lose(reason), "deal.lost")

    def reopen(self, principal: Principal, deal_id: UUID) -> Deal:
        with self._uow:
            deal = self._load_for_write(principal, deal_id)
            version = deal.version
            from_stage = deal.stage_id
            event = deal.reopen(deal.stage_id)
            self._deals.save(deal, expected_version=version)
            self._record_stage_change(principal, deal, from_stage)
            self._emit(event, deal)
            self._record(principal, "deal.reopened", deal)
        return deal

    # --- helpers ------------------------------------------------------------

    def _close(
        self,
        principal: Principal,
        deal_id: UUID,
        action: Callable[[Deal], DomainEvent],
        audit_action: str,
    ) -> Deal:
        with self._uow:
            deal = self._load_for_write(principal, deal_id)
            version = deal.version
            from_stage = deal.stage_id
            event = action(deal)
            self._deals.save(deal, expected_version=version)
            self._record_stage_change(principal, deal, from_stage)
            self._emit(event, deal)
            self._record(principal, audit_action, deal)
        return deal

    def _resolve_pipeline(self, pipeline_id: UUID | None) -> Pipeline:
        pipeline = (
            self._pipelines.get(pipeline_id)
            if pipeline_id is not None
            else self._pipelines.get_default()
        )
        if pipeline is None:
            raise DomainValidationError(
                "No pipeline available. Create one, or mark a pipeline as default."
            )
        return pipeline

    def _money_update(self, deal: Deal, command: EditDeal) -> Money | None:
        if command.amount is None and command.currency is None:
            return None
        return Money(
            command.amount if command.amount is not None else deal.value.amount,
            command.currency if command.currency is not None else deal.value.currency,
        )

    def _load_for_write(self, principal: Principal, deal_id: UUID) -> Deal:
        deal = self.get(principal, deal_id)  # 404 if not visible
        self._policy.require(principal, Permission.DEALS_UPDATE, _ownership(deal))
        return deal

    def _check_version(self, deal: Deal, expected_version: int | None) -> None:
        if expected_version is not None and expected_version != deal.version:
            raise ConflictError("Deal was modified by someone else; reload and retry.")

    def _record_stage_change(
        self, principal: Principal, deal: Deal, from_stage_id: UUID | None
    ) -> None:
        if self._stage_changes is None:
            return
        is_lost = deal.status is DealStatus.LOST
        self._stage_changes.add(
            StageChange.record(
                deal_id=deal.id,
                from_stage_id=from_stage_id,
                to_stage_id=deal.stage_id,
                author_user_id=principal.user_id,
                deal_version=deal.version,
                reason=deal.lost_reason if is_lost else None,
                loss_reason_id=deal.loss_reason_id if is_lost else None,
            )
        )

    def _emit(self, event: DomainEvent, deal: Deal) -> None:
        self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(deal.id))

    def _record(self, principal: Principal, action: str, deal: Deal) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(deal.id),
            )
        )
