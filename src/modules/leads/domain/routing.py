"""Automatic lead distribution (round-robin) inside one department.

The owner's rule: a lead captured by someone who is not a sales manager goes, in turn, to
the active sales managers of the department chosen in settings who are on shift (an open
work session) and not on a planned absence. The head of sales can turn it off.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RoutingRule:
    """Distribution settings of one department. Off unless someone turns it on."""

    department_id: UUID
    enabled: bool = False
    # Who takes part; empty = every active sales manager of the department.
    member_ids: frozenset[UUID] = field(default_factory=frozenset)
    require_on_shift: bool = True
    skip_on_absence: bool = True
    # Leads captured by members without a department (director, imports) go here.
    is_company_intake: bool = False
    # Rotation pointer: the manager who got the previous lead.
    last_assigned_user_id: UUID | None = None
    updated_by: UUID | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    """A department's sales manager and why they may (not) receive the next lead."""

    user_id: UUID
    on_shift: bool
    absent: bool
    selected: bool  # taking part per the rule's member list

    def eligible(self, rule: RoutingRule) -> bool:
        if not self.selected:
            return False
        if rule.require_on_shift and not self.on_shift:
            return False
        return not (rule.skip_on_absence and self.absent)


def candidates(
    rule: RoutingRule,
    managers: Iterable[UUID],
    on_shift: Collection[UUID],
    absent: Collection[UUID],
) -> list[Candidate]:
    """Every active manager of the department in the stable rotation order (by id)."""
    return [
        Candidate(
            user_id=user_id,
            on_shift=user_id in on_shift,
            absent=user_id in absent,
            selected=not rule.member_ids or user_id in rule.member_ids,
        )
        for user_id in sorted(set(managers), key=str)
    ]


def next_assignee(eligible: Iterable[UUID], last: UUID | None) -> UUID | None:
    """The next manager after ``last`` in the stable order, wrapping around.

    Fair across changing shifts: whoever comes after the previous assignee in the order
    gets the lead, even if the previous assignee has since left the shift."""
    ordered = sorted(set(eligible), key=str)
    if not ordered:
        return None
    if last is not None:
        for user_id in ordered:
            if str(user_id) > str(last):
                return user_id
    return ordered[0]
