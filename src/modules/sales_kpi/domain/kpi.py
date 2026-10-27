"""KPI definitions and aggregation semantics (pure domain logic)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from shared.domain.errors import DomainValidationError

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class Aggregation(StrEnum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    RATIO = "ratio"  # sum(numerator) / sum(denominator)


class Period(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@dataclass(frozen=True, slots=True)
class KpiDefinition:
    code: str  # e.g. "deals_won", "lead_conversion_rate"
    name: str
    aggregation: Aggregation
    metric: str  # read-model metric this KPI is computed from
    denominator_metric: str | None = None
    unit: str = ""
    target: Decimal | None = None

    def __post_init__(self) -> None:
        if not _CODE_RE.match(self.code):
            raise DomainValidationError("KPI code must be snake_case, 3-64 chars.")
        if (self.aggregation is Aggregation.RATIO) != (self.denominator_metric is not None):
            raise DomainValidationError(
                "Only RATIO KPIs (and all of them) need a denominator metric."
            )


@dataclass(frozen=True, slots=True)
class MetricSample:
    """One fact from a read model, e.g. a deal amount or a lead conversion (0/1)."""

    metric: str
    value: Decimal
    occurred_at: datetime
    dimensions: Mapping[str, str] = field(default_factory=dict)


def aggregate(definition: KpiDefinition, samples: Sequence[MetricSample]) -> Decimal | None:
    """Compute a KPI value. Returns None when undefined (e.g. average of nothing, 0 denominator)."""
    values = [s.value for s in samples if s.metric == definition.metric]
    match definition.aggregation:
        case Aggregation.COUNT:
            return Decimal(len(values))
        case Aggregation.SUM:
            return sum(values, Decimal(0))
        case Aggregation.AVG:
            return (sum(values, Decimal(0)) / len(values)) if values else None
        case Aggregation.RATIO:
            denominator = sum(
                (s.value for s in samples if s.metric == definition.denominator_metric), Decimal(0)
            )
            return (sum(values, Decimal(0)) / denominator) if denominator else None


@dataclass(frozen=True, slots=True)
class KpiSnapshot:
    """Immutable, idempotently stored result for (kpi, period, period_start, dimensions)."""

    snapshot_id: UUID
    kpi_code: str
    period: Period
    period_start: date
    value: Decimal | None
    dimensions: Mapping[str, str]
    computed_at: datetime
    source_watermark: datetime | None  # newest fact included; enables incremental recompute
