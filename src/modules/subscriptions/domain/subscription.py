"""Subscription aggregate (one per organization). Pure Python: no Django, no ORM."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from modules.subscriptions.domain.plan import PlanCode
from shared.domain.errors import ConflictError, DomainValidationError
from shared.domain.events import DomainEvent


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"


class SubscriptionStateError(ConflictError):
    code = "subscription_invalid_state"


@dataclass(frozen=True, kw_only=True)
class SubscriptionStarted(DomainEvent):
    event_type: ClassVar[str] = "subscriptions.subscription_started"
    organization_id: UUID
    plan_code: str

    def payload(self) -> dict[str, Any]:
        return {"organization_id": str(self.organization_id), "plan_code": self.plan_code}


@dataclass(frozen=True, kw_only=True)
class PlanChanged(DomainEvent):
    event_type: ClassVar[str] = "subscriptions.plan_changed"
    organization_id: UUID
    plan_code: str

    def payload(self) -> dict[str, Any]:
        return {"organization_id": str(self.organization_id), "plan_code": self.plan_code}


@dataclass(frozen=True, kw_only=True)
class SubscriptionCancelled(DomainEvent):
    event_type: ClassVar[str] = "subscriptions.subscription_cancelled"
    organization_id: UUID

    def payload(self) -> dict[str, Any]:
        return {"organization_id": str(self.organization_id)}


@dataclass(slots=True)
class Subscription:
    id: UUID
    organization_id: UUID
    plan_code: PlanCode
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    current_period_end: datetime | None = None
    cancelled_at: datetime | None = None
    version: int = 1

    @classmethod
    def start(
        cls, *, organization_id: UUID, plan_code: PlanCode = PlanCode.FREE
    ) -> tuple[Subscription, SubscriptionStarted]:
        subscription = cls(id=uuid4(), organization_id=organization_id, plan_code=plan_code)
        return subscription, SubscriptionStarted(
            organization_id=organization_id, plan_code=plan_code.value
        )

    def change_plan(self, plan_code: PlanCode) -> PlanChanged:
        if self.status is SubscriptionStatus.CANCELLED:
            raise SubscriptionStateError("Reactivate the subscription before changing its plan.")
        if plan_code == self.plan_code:
            raise DomainValidationError("The subscription is already on this plan.")
        self.plan_code = plan_code
        self.status = SubscriptionStatus.ACTIVE
        self.version += 1
        return PlanChanged(organization_id=self.organization_id, plan_code=plan_code.value)

    def mark_past_due(self) -> None:
        if self.status is not SubscriptionStatus.ACTIVE:
            raise SubscriptionStateError("Only an active subscription can become past due.")
        self.status = SubscriptionStatus.PAST_DUE
        self.version += 1

    def reactivate(self) -> None:
        if self.status is SubscriptionStatus.ACTIVE:
            raise SubscriptionStateError("The subscription is already active.")
        self.status = SubscriptionStatus.ACTIVE
        self.cancelled_at = None
        self.version += 1

    def cancel(self) -> SubscriptionCancelled:
        if self.status is SubscriptionStatus.CANCELLED:
            raise SubscriptionStateError("The subscription is already cancelled.")
        self.status = SubscriptionStatus.CANCELLED
        self.cancelled_at = datetime.now(UTC)
        self.version += 1
        return SubscriptionCancelled(organization_id=self.organization_id)
