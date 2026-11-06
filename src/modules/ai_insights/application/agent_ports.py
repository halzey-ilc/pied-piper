from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.domain.agent import AgentAction, AgentRun, Subject
from modules.ai_insights.domain.agent_questions import DataAnswer, Question


class SubjectReader(Protocol):
    def deal(self, principal: Principal, deal_id: UUID) -> Subject:
        """Raises NotFoundError if the principal cannot see the deal."""
        ...

    def deals_named(self, principal: Principal, names: list[str]) -> list[Subject]:
        """The deals the principal can see that match the first of ``names`` matching any.
        Several results are for the user to choose from: the assistant must not guess."""
        ...

    def customer(self, principal: Principal, customer_id: UUID) -> Subject:
        """Raises NotFoundError if the principal cannot see the customer."""
        ...


class DataAnswers(Protocol):
    def answer(
        self, principal: Principal, question: Question, *, today: date, language: str
    ) -> DataAnswer:
        """Counted by the owning modules as the principal. Raises AccessDeniedError if
        the principal may not see this data."""
        ...


class ToolExecutor(Protocol):
    def execute(self, principal: Principal, action: AgentAction, subject: Subject) -> str:
        """Run the action through the owning module's service as the principal and
        return a ref of what it created. Raises the module's own errors
        (AccessDeniedError, NotFoundError, DomainValidationError, ...)."""
        ...


class AgentRunRepository(Protocol):
    def add(self, run: AgentRun) -> None: ...

    def get(self, run_id: UUID) -> AgentRun | None: ...

    def save(self, run: AgentRun, expected_version: int) -> None: ...


class AgentScheduler(Protocol):
    def schedule_submit(self, *, tenant_slug: str, run_id: UUID) -> None: ...

    def schedule_poll(self, *, tenant_slug: str, run_id: UUID, delay_seconds: float) -> None: ...
