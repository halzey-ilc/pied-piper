"""Risk rules: pure functions over snapshots (no database)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from modules.risk.domain.rules import (
    AuditEvent,
    OpenDeal,
    SignalKind,
    Thresholds,
    WonDeal,
    counts,
    detect,
)
from shared.domain.errors import DomainValidationError

TODAY = date(2026, 10, 29)


def _events(action: str, actor: object, n: int, day: date = TODAY) -> list[AuditEvent]:
    return [AuditEvent(action, actor, str(uuid4()), day) for _ in range(n)]  # type: ignore[arg-type]


def test_each_rule_fires_at_its_threshold_and_not_below() -> None:
    alice, bob = uuid4(), uuid4()
    quiet_win = WonDeal(uuid4(), alice, TODAY, has_activity=False)
    logged_win = WonDeal(uuid4(), alice, TODAY, has_activity=True)
    reopened = str(uuid4())
    events = (
        _events("lead.disqualified", alice, 10)  # == threshold
        + _events("lead.disqualified", bob, 9)  # below
        + _events("deal.lost", bob, 5)
        + _events("invoice.voided", alice, 3)
        + [AuditEvent("deal.reopened", bob, reopened, TODAY)] * 2
        + [AuditEvent("deal.reopened", bob, "not-a-uuid", TODAY)]
    )
    signals = detect(
        today=TODAY,
        thresholds=Thresholds(),
        won=[quiet_win, logged_win],
        open_deals=[],
        events=events,
    )
    found = {(s.kind, s.subject_id) for s in signals}
    assert (SignalKind.WON_WITHOUT_ACTIVITY, quiet_win.id) in found
    assert (SignalKind.WON_WITHOUT_ACTIVITY, logged_win.id) not in found
    assert (SignalKind.BULK_DISQUALIFICATION, alice) in found
    assert (SignalKind.BULK_DISQUALIFICATION, bob) not in found
    assert (SignalKind.DEALS_LOST_BURST, bob) in found
    assert (SignalKind.INVOICES_VOIDED, alice) in found
    [reopen] = [s for s in signals if s.kind is SignalKind.REOPENED_AFTER_CLOSE]
    assert str(reopen.subject_id) == reopened and reopen.value == 2
    assert counts(signals)["amount_outlier"] == 0


def test_bursts_count_per_day_not_per_window() -> None:
    alice = uuid4()
    events = _events("lead.disqualified", alice, 6, date(2026, 10, 1)) + _events(
        "lead.disqualified", alice, 6, date(2026, 10, 2)
    )
    signals = detect(today=TODAY, thresholds=Thresholds(), won=[], open_deals=[], events=events)
    assert signals == []


def test_amount_outlier_needs_a_sample_and_uses_the_median() -> None:
    owner = uuid4()
    usual = [OpenDeal(uuid4(), owner, Decimal(1000)) for _ in range(9)]
    big = OpenDeal(uuid4(), owner, Decimal(6000))
    # Nine usual deals plus one big: only ten deals, the median is 1000.
    signals = detect(
        today=TODAY, thresholds=Thresholds(), won=[], open_deals=[*usual, big], events=[]
    )
    [outlier] = signals
    assert outlier.subject_id == big.id and outlier.value == 6.0 and outlier.threshold == 5
    # Fewer than ten open deals: no basis for "unusual".
    assert (
        detect(
            today=TODAY, thresholds=Thresholds(), won=[], open_deals=[*usual[:5], big], events=[]
        )
        == []
    )


def test_thresholds_are_validated() -> None:
    with pytest.raises(DomainValidationError):
        Thresholds(window_days=0)
    with pytest.raises(DomainValidationError):
        Thresholds(amount_multiplier=1)
