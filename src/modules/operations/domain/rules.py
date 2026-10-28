"""Operations rules. Pure functions over snapshots of CRM records.

Rules (thresholds are per company, see ``Thresholds``):

* ``stale_lead``      - a new or qualified lead untouched for >= stale_lead_days;
* ``overdue_task``    - an open task whose due date has passed;
* ``deal_without_next_step`` - an open deal with no open, not-yet-overdue task;
* ``stalled_deal``    - an open deal untouched for >= stalled_deal_days.

"Untouched" means the record's ``updated_at`` did not change. The CRM keeps no
stage history, so a stalled deal is one with no update of any kind, not
specifically one stuck in a stage.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from shared.domain.errors import DomainValidationError

MAX_THRESHOLD_DAYS = 365


class FindingKind(StrEnum):
    STALE_LEAD = "stale_lead"
    OVERDUE_TASK = "overdue_task"
    DEAL_WITHOUT_NEXT_STEP = "deal_without_next_step"
    STALLED_DEAL = "stalled_deal"


class RecordType(StrEnum):
    LEAD = "lead"
    DEAL = "deal"
    TASK = "task"


@dataclass(frozen=True, slots=True)
class Thresholds:
    stale_lead_days: int = 3
    stalled_deal_days: int = 14

    def __post_init__(self) -> None:
        for name in ("stale_lead_days", "stalled_deal_days"):
            value = getattr(self, name)
            if not 1 <= value <= MAX_THRESHOLD_DAYS:
                raise DomainValidationError(f"{name} must be between 1 and {MAX_THRESHOLD_DAYS}.")


@dataclass(frozen=True, slots=True)
class LeadSnapshot:
    id: UUID
    owner_user_id: UUID
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DealSnapshot:
    id: UUID
    owner_user_id: UUID
    updated_at: datetime
    has_next_step: bool  # an open task on the deal that is not overdue


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    id: UUID
    owner_user_id: UUID
    due_date: date
    deal_id: UUID | None  # set when the task belongs to a deal


@dataclass(frozen=True, slots=True)
class Finding:
    kind: FindingKind
    record_type: RecordType
    record_id: UUID
    owner_user_id: UUID
    age_days: int
    deal_id: UUID | None = None


def _days(since: datetime, now: datetime) -> int:
    return max((now - since).days, 0)


def detect(
    *,
    now: datetime,
    thresholds: Thresholds,
    leads: Iterable[LeadSnapshot],
    deals: Iterable[DealSnapshot],
    tasks: Iterable[TaskSnapshot],
) -> list[Finding]:
    """Apply every rule; oldest problems first within the whole list."""
    today = now.date()
    findings: list[Finding] = []
    for lead in leads:
        age = _days(lead.updated_at, now)
        if age >= thresholds.stale_lead_days:
            findings.append(
                Finding(FindingKind.STALE_LEAD, RecordType.LEAD, lead.id, lead.owner_user_id, age)
            )
    for task in tasks:
        if task.due_date < today:
            findings.append(
                Finding(
                    FindingKind.OVERDUE_TASK,
                    RecordType.TASK,
                    task.id,
                    task.owner_user_id,
                    (today - task.due_date).days,
                    deal_id=task.deal_id,
                )
            )
    for deal in deals:
        age = _days(deal.updated_at, now)
        if not deal.has_next_step:
            findings.append(
                Finding(
                    FindingKind.DEAL_WITHOUT_NEXT_STEP,
                    RecordType.DEAL,
                    deal.id,
                    deal.owner_user_id,
                    age,
                    deal_id=deal.id,
                )
            )
        if age >= thresholds.stalled_deal_days:
            findings.append(
                Finding(
                    FindingKind.STALLED_DEAL,
                    RecordType.DEAL,
                    deal.id,
                    deal.owner_user_id,
                    age,
                    deal_id=deal.id,
                )
            )
    findings.sort(key=lambda f: (-f.age_days, f.kind.value, str(f.record_id)))
    return findings


def counts(findings: Iterable[Finding]) -> dict[str, int]:
    result = dict.fromkeys((k.value for k in FindingKind), 0)
    for f in findings:
        result[f.kind.value] += 1
    return result
