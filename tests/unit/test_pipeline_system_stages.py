"""System stages in the pipeline aggregate: «Продажа» locked and last, «Встреча перенесена»
movable and switchable but never renamed or removed."""

from __future__ import annotations

import pytest

from modules.sales_pipeline.domain.pipeline import (
    Pipeline,
    StageDefinition,
    StageKind,
    SystemStage,
    SystemStageLockedError,
)


def _pipeline(*definitions: StageDefinition) -> Pipeline:
    return Pipeline.create(name="P", stages=list(definitions), is_default=True)


def test_first_won_stage_becomes_sale_and_goes_last() -> None:
    pipeline = _pipeline(
        StageDefinition("New", 10),
        StageDefinition("Won", 100, StageKind.WON),
        StageDefinition("Won again", 100, StageKind.WON),
        StageDefinition("Lost", 0, StageKind.LOST),
    )
    names = [s.name for s in sorted(pipeline.stages, key=lambda s: s.position)]
    assert names == ["New", "Встреча перенесена", "Won again", "Lost", "Продажа"]
    sale = pipeline.system_stage(SystemStage.SALE)
    assert sale is not None and sale.kind is StageKind.WON and sale.is_system


def test_sale_is_created_when_missing_and_name_clashes_are_renamed() -> None:
    pipeline = _pipeline(StageDefinition("Продажа", 10), StageDefinition("Next", 20))
    names = [s.name for s in sorted(pipeline.stages, key=lambda s: s.position)]
    assert names == ["Продажа (2)", "Next", "Встреча перенесена", "Продажа"]


def test_sale_cannot_be_renamed_switched_off_removed_or_moved() -> None:
    pipeline = _pipeline(StageDefinition("New", 10))
    sale = pipeline.system_stage(SystemStage.SALE)
    assert sale is not None
    with pytest.raises(SystemStageLockedError):
        pipeline.update_stage(sale.id, name="Оплата")
    with pytest.raises(SystemStageLockedError):
        pipeline.set_stage_active(sale.id, False)
    with pytest.raises(SystemStageLockedError):
        pipeline.remove_stage(sale.id)
    ordered = [s.id for s in sorted(pipeline.stages, key=lambda s: s.position)]
    with pytest.raises(SystemStageLockedError):
        pipeline.reorder([sale.id, *ordered[:-1]])
    added = pipeline.add_stage(StageDefinition("After", 50), position=99)
    assert max(pipeline.stages, key=lambda s: s.position).id == sale.id
    assert added.position == len(pipeline.stages) - 2


def test_meeting_rescheduled_can_move_and_switch_off_but_not_be_renamed_or_removed() -> None:
    pipeline = _pipeline(StageDefinition("New", 10), StageDefinition("Demo", 30))
    rescheduled = pipeline.system_stage(SystemStage.MEETING_RESCHEDULED)
    assert rescheduled is not None and rescheduled.kind is StageKind.OPEN
    ordered = [s.id for s in sorted(pipeline.stages, key=lambda s: s.position)]
    assert pipeline.reorder([ordered[0], rescheduled.id, ordered[1], ordered[3]])
    assert pipeline.set_stage_active(rescheduled.id, False)
    with pytest.raises(SystemStageLockedError):
        pipeline.update_stage(rescheduled.id, name="Перенос")
    with pytest.raises(SystemStageLockedError):
        pipeline.remove_stage(rescheduled.id)
