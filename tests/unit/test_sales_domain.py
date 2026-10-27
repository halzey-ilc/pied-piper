from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from modules.sales_pipeline.domain.deal import (
    Deal,
    DealCreated,
    DealStateError,
    DealWon,
)
from modules.sales_pipeline.domain.pipeline import (
    Pipeline,
    StageDefinition,
    StageNotInPipelineError,
)
from shared.domain.errors import DomainValidationError
from shared.domain.value_objects import Money


def _pipeline() -> Pipeline:
    return Pipeline.create(
        name="Standard",
        stages=[StageDefinition("New", 10), StageDefinition("Negotiation", 50)],
        is_default=True,
    )


def _open_deal(pipeline: Pipeline) -> Deal:
    deal, _ = Deal.open(
        title="Acme deal",
        pipeline_id=pipeline.id,
        stage_id=pipeline.initial_stage.id,
        value=Money(Decimal("1000"), "USD"),
        owner_user_id=uuid4(),
    )
    return deal


# --- Money ------------------------------------------------------------------


def test_money_validates_and_normalises() -> None:
    assert str(Money(Decimal("10"), "usd")) == "10.00 USD"
    assert Money("1200.5", "KZT").amount == Decimal("1200.50")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("amount", "currency"),
    [(Decimal("-1"), "USD"), (Decimal("1"), "US"), (Decimal("1"), "dollars")],
)
def test_money_rejects_bad_values(amount: Decimal, currency: str) -> None:
    with pytest.raises(DomainValidationError):
        Money(amount, currency)


# --- Pipeline ---------------------------------------------------------------


def test_pipeline_create_assigns_positions_and_initial_stage() -> None:
    pipeline = _pipeline()
    assert [s.position for s in pipeline.stages] == [0, 1]
    assert pipeline.initial_stage.name == "New"
    assert pipeline.stage(pipeline.stages[1].id).name == "Negotiation"


def test_pipeline_rejects_empty_and_duplicate_stages() -> None:
    with pytest.raises(DomainValidationError):
        Pipeline.create(name="Empty", stages=[])
    with pytest.raises(DomainValidationError):
        Pipeline.create(name="Dup", stages=[StageDefinition("A"), StageDefinition("a")])
    with pytest.raises(DomainValidationError):
        Pipeline.create(name="BadProb", stages=[StageDefinition("A", 101)])


def test_pipeline_unknown_stage_raises() -> None:
    with pytest.raises(StageNotInPipelineError):
        _pipeline().stage(uuid4())


# --- Deal lifecycle ---------------------------------------------------------


def test_open_deal_starts_open_in_initial_stage() -> None:
    pipeline = _pipeline()
    deal, event = Deal.open(
        title="  Big   deal ",
        pipeline_id=pipeline.id,
        stage_id=pipeline.initial_stage.id,
        value=Money(Decimal("500"), "USD"),
        owner_user_id=uuid4(),
    )
    assert deal.title == "Big deal"
    assert deal.status.value == "open"
    assert deal.stage_id == pipeline.initial_stage.id
    assert isinstance(event, DealCreated)


def test_move_stage_only_when_open_and_tracks_change() -> None:
    pipeline = _pipeline()
    deal = _open_deal(pipeline)
    target = pipeline.stages[1].id
    event = deal.move_to(target)
    assert event is not None and event.to_stage_id == target
    assert deal.stage_id == target
    assert deal.move_to(target) is None  # no-op


def test_win_closes_deal_and_emits_amount() -> None:
    deal = _open_deal(_pipeline())
    event = deal.win()
    assert isinstance(event, DealWon)
    assert event.payload() == {"deal_id": str(deal.id), "amount": "1000.00", "currency": "USD"}
    assert deal.status.value == "won"
    assert deal.closed_at is not None


def test_lose_requires_reason_and_closes() -> None:
    deal = _open_deal(_pipeline())
    with pytest.raises(DomainValidationError):
        deal.lose("   ")
    deal.lose("price too high")
    assert deal.status.value == "lost"
    assert deal.lost_reason == "price too high"


def test_closed_deal_is_read_only_until_reopened() -> None:
    pipeline = _pipeline()
    deal = _open_deal(pipeline)
    deal.win()
    with pytest.raises(DealStateError):
        deal.move_to(pipeline.stages[1].id)
    with pytest.raises(DealStateError):
        deal.win()
    with pytest.raises(DealStateError):
        deal.update_details(title="x")
    deal.reopen(pipeline.initial_stage.id)
    assert deal.status.value == "open"
    assert deal.closed_at is None
    # Now editable again.
    assert deal.update_details(title="Reworked") is True


def test_cannot_reopen_an_open_deal() -> None:
    pipeline = _pipeline()
    with pytest.raises(DealStateError):
        _open_deal(pipeline).reopen(pipeline.initial_stage.id)


def test_update_details_can_clear_optional_fields() -> None:
    deal = _open_deal(_pipeline())
    customer = uuid4()
    assert deal.update_details(customer_id=customer, set_customer=True) is True
    assert deal.customer_id == customer
    assert deal.update_details(customer_id=None, set_customer=True) is True
    assert deal.customer_id is None
