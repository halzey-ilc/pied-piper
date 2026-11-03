"""Closing a deal as a SALE: the only way onto the system stage «Продажа».

Owner decision of 03.11.2026: a deal becomes won only after its payment is confirmed.
Moving a deal to «Продажа» by hand is refused (409 ``sale_requires_payment``, see
``DealService.move_stage``); instead the payments module calls ``close_deal_as_sale`` when
a payment recorded from «Продажа» is confirmed (manually by the head of sales / director,
or by the automatic receipt verifier).

This is an internal, system-initiated transition: it deliberately does NOT go through
``DealService.move_stage`` (and so is not subject to its «Продажа» guard or to the actor's
deal permissions — the authorization was the payment verification itself). It still
writes everything a normal close writes: the deal (won, closed_at, version), the stage
history entry, the outbox event and the audit entry.
"""

from __future__ import annotations

from uuid import UUID

from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.sales_pipeline.application.ports import (
    DealRepository,
    PipelineRepository,
    StageChangeRepository,
)
from modules.sales_pipeline.domain.deal import DealStatus
from modules.sales_pipeline.domain.pipeline import Pipeline, Stage, StageKind
from modules.sales_pipeline.domain.stage_change import StageChange
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import NotFoundError

AGGREGATE = "deal"
SALE_STAGE_CODE = "sale"  # SystemStage.SALE


def sale_stage(pipeline: Pipeline) -> Stage | None:
    """The pipeline's system «Продажа» stage; for a pipeline created before system stages,
    its last active won stage."""
    for stage in pipeline.stages:
        if getattr(stage, "system_code", None) == SALE_STAGE_CODE:
            return stage
    won = [s for s in pipeline.stages if s.kind is StageKind.WON and s.is_active]
    return max(won, key=lambda s: s.position) if won else None


class SaleClosing:
    def __init__(
        self,
        deals: DealRepository,
        pipelines: PipelineRepository,
        stage_changes: StageChangeRepository,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
    ) -> None:
        self._deals = deals
        self._pipelines = pipelines
        self._stage_changes = stage_changes
        self._uow = uow
        self._outbox = outbox
        self._audit = audit

    def close_deal_as_sale(self, deal_id: UUID, actor_user_id: UUID) -> bool:
        """Win the deal on its pipeline's «Продажа» stage. Returns False (and changes
        nothing) when the deal is already closed — won by an earlier payment, or lost:
        a confirmed payment counts as money either way, but a lost deal is reopened by a
        person, not by the system."""
        with self._uow:
            deal = self._deals.get(deal_id)
            if deal is None:
                raise NotFoundError("Deal not found.")
            if deal.status is not DealStatus.OPEN:
                return False
            pipeline = self._pipelines.get(deal.pipeline_id)
            stage = sale_stage(pipeline) if pipeline is not None else None
            version = deal.version
            from_stage = deal.stage_id
            event = deal.win(stage_id=stage.id if stage is not None else None)
            self._deals.save(deal, expected_version=version)
            self._stage_changes.add(
                StageChange.record(
                    deal_id=deal.id,
                    from_stage_id=from_stage,
                    to_stage_id=deal.stage_id,
                    author_user_id=actor_user_id,
                    deal_version=deal.version,
                )
            )
            self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(deal.id))
            self._audit.record(
                AuditEntry(
                    actor_user_id=actor_user_id,
                    action="deal.won",
                    resource_type=AGGREGATE,
                    resource_id=str(deal.id),
                )
            )
        return True
