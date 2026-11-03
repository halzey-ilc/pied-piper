"""The "next step" rule (owner decision, 03.11.2026): after a meeting is held and when a deal
moves to another open stage, the work must continue with a task — a title, a due date and an
assignee — unless the deal (or customer) already has an open task with a due date.

Other modules ask for it through the ``NextSteps`` port; the activities context implements it,
so the task is created by the activity use case inside the caller's transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from shared.domain.errors import DomainValidationError


class NextStepRequiredError(DomainValidationError):
    code = "next_step_required"


@dataclass(frozen=True, slots=True)
class NextStep:
    title: str
    due_date: date
    assignee_user_id: UUID | None = None


class NextSteps(Protocol):
    def has_open_dated_task(self, subject_type: str, subject_id: UUID) -> bool:
        """An open task with a due date is already attached to the deal/customer."""
        ...

    def create(
        self,
        principal: Principal,
        subject_type: str,
        subject_id: UUID,
        step: NextStep,
        *,
        default_assignee: UUID,
    ) -> UUID:
        """Create the task; ``default_assignee`` is used when the step names nobody."""
        ...


def ensure_next_step(
    steps: NextSteps,
    principal: Principal,
    subject_type: str,
    subject_id: UUID,
    step: NextStep | None,
    *,
    default_assignee: UUID,
) -> UUID | None:
    """Create the given next step, or check one is already planned (else 400)."""
    if step is not None:
        return steps.create(
            principal, subject_type, subject_id, step, default_assignee=default_assignee
        )
    if not steps.has_open_dated_task(subject_type, subject_id):
        raise NextStepRequiredError(
            "Plan the next step: a task with a title, a due date and an assignee."
        )
    return None
