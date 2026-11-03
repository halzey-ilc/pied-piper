"""Funnel, loss and day-score analytics of the cabinet: value objects and the pure rules
(conversion and the bottleneck). Every number comes from stored records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

# A stage is judged for the bottleneck only when enough deals passed through it.
MIN_REACHED_FOR_BOTTLENECK = 3


@dataclass(slots=True)
class StageStat:
    stage_id: UUID
    name: str
    kind: str  # open | won | lost
    position: int
    open_deals: int = 0
    open_amount: Decimal = Decimal(0)  # company currency only
    median_age_days: Decimal | None = None  # days since entering the current stage
    stale_deals: int = 0  # open deals on the stage longer than the threshold
    reached: int = 0  # deals that entered the stage in the period
    reached_previous: int = 0  # the same for the previous period of equal length
    moved_on: int = 0  # of `reached`: later reached a later stage or a won stage
    conversion: Decimal | None = None  # moved_on / reached
    is_bottleneck: bool = False


@dataclass(frozen=True, slots=True)
class FunnelReport:
    pipeline_id: UUID
    pipeline_name: str
    start: date
    end: date
    previous_start: date
    previous_end: date
    stale_days: int
    stages: list[StageStat]
    won: int
    lost: int
    won_previous: int
    lost_previous: int


def finish_funnel(stages: list[StageStat]) -> None:
    """Conversion per stage and the bottleneck: the open stage with the lowest conversion among
    those at least MIN_REACHED_FOR_BOTTLENECK deals entered; when none qualifies — the open stage
    with the most stale deals (if any)."""
    for stage in stages:
        stage.conversion = (
            (Decimal(stage.moved_on) / Decimal(stage.reached)).quantize(Decimal("0.001"))
            if stage.reached
            else None
        )
    candidates = [
        s
        for s in stages
        if s.kind == "open" and s.reached >= MIN_REACHED_FOR_BOTTLENECK and s.conversion is not None
    ]
    if candidates:
        worst = min(candidates, key=lambda s: (s.conversion, -s.reached))
        worst.is_bottleneck = True
        return
    stale = [s for s in stages if s.kind == "open" and s.stale_deals > 0]
    if stale:
        max(stale, key=lambda s: s.stale_deals).is_bottleneck = True


@dataclass(frozen=True, slots=True)
class ReasonCount:
    reason: str
    total: int
    by_month: dict[str, int] = field(default_factory=dict)  # YYYY-MM -> count


@dataclass(frozen=True, slots=True)
class MeetingReasons:
    total: int  # meetings that started in the period
    cancelled: int
    no_show: int
    cancel_reasons: list[ReasonCount]
    no_show_reasons: list[ReasonCount]


@dataclass(frozen=True, slots=True)
class LeadResponse:
    leads: int  # created in the period
    responded: int  # with a first response (activity or status change) by now
    median_minutes: Decimal | None
    within_15_min: int
    within_60_min: int


@dataclass(frozen=True, slots=True)
class LossReport:
    start: date
    end: date
    previous_start: date
    previous_end: date
    months: list[str]
    lost_deals: int
    lost_previous: int
    loss_reasons: list[ReasonCount]
    meetings_total: int
    meetings_cancelled: int
    meetings_no_show: int
    cancelled_previous: int
    no_show_previous: int
    cancel_reasons: list[ReasonCount]
    no_show_reasons: list[ReasonCount]
    lead_response: LeadResponse


@dataclass(frozen=True, slots=True)
class DayScorePoint:
    day: date
    points: int | None  # None — silent day, planned absence or a day in the future
    grade: int | None
    reason: str | None  # "silent" | "absent" | None


@dataclass(frozen=True, slots=True)
class ScoreHistory:
    user_id: UUID
    start: date
    end: date
    points: list[DayScorePoint]
    generated_at: datetime
