"""Operations rules: pure detection over snapshots."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from modules.operations.domain.rules import (
    DealSnapshot,
    FindingKind,
    LeadSnapshot,
    TaskSnapshot,
    Thresholds,
    counts,
    detect,
)
from shared.domain.errors import DomainValidationError

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
OWNER = uuid4()


def _ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def test_each_rule_and_its_threshold() -> None:
    th = Thresholds(stale_lead_days=3, stalled_deal_days=14)
    fresh_lead = LeadSnapshot(uuid4(), OWNER, _ago(2))
    old_lead = LeadSnapshot(uuid4(), OWNER, _ago(3))
    healthy_deal = DealSnapshot(uuid4(), OWNER, _ago(1), has_next_step=True)
    no_step_deal = DealSnapshot(uuid4(), OWNER, _ago(1), has_next_step=False)
    stalled_deal = DealSnapshot(uuid4(), OWNER, _ago(20), has_next_step=True)
    due_today = TaskSnapshot(uuid4(), OWNER, NOW.date(), None)
    overdue = TaskSnapshot(uuid4(), OWNER, NOW.date() - timedelta(days=5), no_step_deal.id)

    findings = detect(
        now=NOW,
        thresholds=th,
        leads=[fresh_lead, old_lead],
        deals=[healthy_deal, no_step_deal, stalled_deal],
        tasks=[due_today, overdue],
    )
    by_kind = {(f.kind, f.record_id) for f in findings}
    assert by_kind == {
        (FindingKind.STALE_LEAD, old_lead.id),
        (FindingKind.DEAL_WITHOUT_NEXT_STEP, no_step_deal.id),
        (FindingKind.STALLED_DEAL, stalled_deal.id),
        (FindingKind.OVERDUE_TASK, overdue.id),
    }
    overdue_finding = next(f for f in findings if f.kind is FindingKind.OVERDUE_TASK)
    assert overdue_finding.age_days == 5 and overdue_finding.deal_id == no_step_deal.id
    # Oldest problems first.
    assert [f.age_days for f in findings] == sorted((f.age_days for f in findings), reverse=True)
    assert counts(findings) == {
        "stale_lead": 1,
        "overdue_task": 1,
        "deal_without_next_step": 1,
        "stalled_deal": 1,
    }


def test_deal_can_be_both_stalled_and_without_next_step() -> None:
    deal = DealSnapshot(uuid4(), OWNER, _ago(30), has_next_step=False)
    kinds = {
        f.kind for f in detect(now=NOW, thresholds=Thresholds(), leads=[], deals=[deal], tasks=[])
    }
    assert kinds == {FindingKind.STALLED_DEAL, FindingKind.DEAL_WITHOUT_NEXT_STEP}


def test_company_thresholds_change_the_result() -> None:
    lead = LeadSnapshot(uuid4(), OWNER, _ago(5))
    assert (
        detect(now=NOW, thresholds=Thresholds(stale_lead_days=7), leads=[lead], deals=[], tasks=[])
        == []
    )
    assert (
        len(
            detect(
                now=NOW, thresholds=Thresholds(stale_lead_days=5), leads=[lead], deals=[], tasks=[]
            )
        )
        == 1
    )


def test_empty_input_counts_every_kind_as_zero() -> None:
    assert set(counts([]).values()) == {0} and len(counts([])) == len(FindingKind)


@pytest.mark.parametrize("bad", [0, -1, 366])
def test_thresholds_are_bounded(bad: int) -> None:
    with pytest.raises(DomainValidationError):
        Thresholds(stale_lead_days=bad)
    with pytest.raises(DomainValidationError):
        Thresholds(stalled_deal_days=bad)


def test_future_timestamps_do_not_produce_negative_age() -> None:
    lead = LeadSnapshot(uuid4(), OWNER, NOW + timedelta(hours=1))
    task = TaskSnapshot(uuid4(), OWNER, date(2026, 10, 29), None)
    assert detect(now=NOW, thresholds=Thresholds(), leads=[lead], deals=[], tasks=[task]) == []
