"""Risk rules (AI Risk & Anomaly Detection, first version). Pure functions over
snapshots of CRM records and audit entries.

A signal is a reason for a person to look, **not** evidence that anyone broke
a rule: a won deal without recorded activity may simply have been handled by
phone without logging it. Signals carry ids and numbers only.

Rules (thresholds are per company, see ``Thresholds``; all look at the last
``window_days``):

* ``won_without_activity``  - a deal won in the window with no note, task, call
  or meeting recorded on it;
* ``reopened_after_close``  - a closed deal reopened in the window (audit log);
* ``amount_outlier``        - an open deal at least ``amount_multiplier`` times the
  median open deal (only with at least ``MIN_SAMPLE`` open deals);
* ``bulk_disqualification`` - one person disqualified at least
  ``disqualify_per_day`` leads on one day;
* ``deals_lost_burst``      - one person marked at least ``lost_per_day`` deals
  lost on one day;
* ``invoices_voided``       - one person voided at least ``voids_per_window``
  invoices in the window.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from statistics import median
from uuid import UUID

from shared.domain.errors import DomainValidationError

MIN_SAMPLE = 10


class SignalKind(StrEnum):
    WON_WITHOUT_ACTIVITY = "won_without_activity"
    REOPENED_AFTER_CLOSE = "reopened_after_close"
    AMOUNT_OUTLIER = "amount_outlier"
    BULK_DISQUALIFICATION = "bulk_disqualification"
    DEALS_LOST_BURST = "deals_lost_burst"
    INVOICES_VOIDED = "invoices_voided"


class SubjectType(StrEnum):
    DEAL = "deal"
    USER = "user"


_LIMITS = {
    "window_days": (1, 365),
    "amount_multiplier": (2, 100),
    "disqualify_per_day": (2, 1000),
    "lost_per_day": (2, 1000),
    "voids_per_window": (1, 1000),
}


@dataclass(frozen=True, slots=True)
class Thresholds:
    window_days: int = 30
    amount_multiplier: int = 5
    disqualify_per_day: int = 10
    lost_per_day: int = 5
    voids_per_window: int = 3

    def __post_init__(self) -> None:
        for name, (low, high) in _LIMITS.items():
            value = getattr(self, name)
            if not low <= value <= high:
                raise DomainValidationError(f"{name} must be between {low} and {high}.")


@dataclass(frozen=True, slots=True)
class WonDeal:
    id: UUID
    owner_user_id: UUID
    closed_on: date
    has_activity: bool


@dataclass(frozen=True, slots=True)
class OpenDeal:
    id: UUID
    owner_user_id: UUID
    amount: Decimal


@dataclass(frozen=True, slots=True)
class AuditEvent:
    action: str  # e.g. "deal.reopened", "lead.disqualified", "deal.lost", "invoice.voided"
    actor_user_id: UUID | None
    resource_id: str
    occurred_on: date


@dataclass(frozen=True, slots=True)
class Signal:
    kind: SignalKind
    subject_type: SubjectType
    subject_id: UUID
    value: float  # the measured number (count, ratio)
    threshold: float  # the company threshold it reached
    on: date  # the day it concerns (latest day for window rules)
    actor_user_id: UUID | None = None  # whose action it concerns, if any

    @property
    def ref(self) -> str:
        return f"{self.subject_type.value}:{self.subject_id}"


def _per_actor_day(events: Iterable[AuditEvent], action: str) -> Counter[tuple[UUID, date]]:
    return Counter(
        (e.actor_user_id, e.occurred_on)
        for e in events
        if e.action == action and e.actor_user_id is not None
    )


def detect(
    *,
    today: date,
    thresholds: Thresholds,
    won: Iterable[WonDeal],
    open_deals: Iterable[OpenDeal],
    events: Iterable[AuditEvent],
) -> list[Signal]:
    """Apply every rule; newest first."""
    events = list(events)
    signals = [
        *_won_without_activity(won),
        *_reopened(events),
        *_amount_outliers(list(open_deals), thresholds, today),
        *_bursts(events, thresholds),
        *_voids(events, thresholds),
    ]
    signals.sort(key=lambda s: (s.on, s.kind.value, str(s.subject_id)), reverse=True)
    return signals


def _won_without_activity(won: Iterable[WonDeal]) -> list[Signal]:
    return [
        Signal(
            SignalKind.WON_WITHOUT_ACTIVITY,
            SubjectType.DEAL,
            d.id,
            0,
            0,
            d.closed_on,
            d.owner_user_id,
        )
        for d in won
        if not d.has_activity
    ]


def _reopened(events: list[AuditEvent]) -> list[Signal]:
    signals: list[Signal] = []
    reopened: dict[str, list[AuditEvent]] = {}
    for e in events:
        if e.action == "deal.reopened":
            reopened.setdefault(e.resource_id, []).append(e)
    for deal_id, items in reopened.items():
        try:
            subject = UUID(deal_id)
        except ValueError:
            continue
        last = max(items, key=lambda e: e.occurred_on)
        signals.append(
            Signal(
                SignalKind.REOPENED_AFTER_CLOSE,
                SubjectType.DEAL,
                subject,
                len(items),
                1,
                last.occurred_on,
                last.actor_user_id,
            )
        )
    return signals


def _amount_outliers(opened: list[OpenDeal], thresholds: Thresholds, today: date) -> list[Signal]:
    amounts = [d.amount for d in opened if d.amount > 0]
    if len(amounts) < MIN_SAMPLE:
        return []  # too few deals to say what is usual
    typical = Decimal(median(amounts))
    if typical <= 0:
        return []
    return [
        Signal(
            SignalKind.AMOUNT_OUTLIER,
            SubjectType.DEAL,
            d.id,
            round(float(d.amount / typical), 1),
            thresholds.amount_multiplier,
            today,
            d.owner_user_id,
        )
        for d in opened
        if d.amount >= typical * thresholds.amount_multiplier
    ]


def _bursts(events: list[AuditEvent], thresholds: Thresholds) -> list[Signal]:
    signals: list[Signal] = []
    for kind, action, limit in (
        (SignalKind.BULK_DISQUALIFICATION, "lead.disqualified", thresholds.disqualify_per_day),
        (SignalKind.DEALS_LOST_BURST, "deal.lost", thresholds.lost_per_day),
    ):
        for (actor, day), count in _per_actor_day(events, action).items():
            if count >= limit:
                signals.append(Signal(kind, SubjectType.USER, actor, count, limit, day, actor))
    return signals


def _voids(events: list[AuditEvent], thresholds: Thresholds) -> list[Signal]:
    signals: list[Signal] = []
    voids: Counter[UUID] = Counter()
    last_void: dict[UUID, date] = {}
    for e in events:
        if e.action == "invoice.voided" and e.actor_user_id is not None:
            voids[e.actor_user_id] += 1
            last_void[e.actor_user_id] = max(
                last_void.get(e.actor_user_id, e.occurred_on), e.occurred_on
            )
    for actor, count in voids.items():
        if count >= thresholds.voids_per_window:
            signals.append(
                Signal(
                    SignalKind.INVOICES_VOIDED,
                    SubjectType.USER,
                    actor,
                    count,
                    thresholds.voids_per_window,
                    last_void[actor],
                    actor,
                )
            )
    return signals


def counts(signals: Iterable[Signal]) -> dict[str, int]:
    c = Counter(s.kind.value for s in signals)
    return {k.value: c.get(k.value, 0) for k in SignalKind}


def window_start(now: datetime, thresholds: Thresholds) -> datetime:
    return now - timedelta(days=thresholds.window_days)
