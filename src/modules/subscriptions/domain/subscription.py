"""Subscription aggregate (one per organization). Pure Python: no Django, no ORM."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from modules.subscriptions.domain.plan import LIMIT_METRICS, UNLIMITED, Feature, Plan, PlanCode
from shared.domain.errors import AccessDeniedError, ConflictError, DomainValidationError
from shared.domain.events import DomainEvent


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"


class SubscriptionStateError(ConflictError):
    code = "subscription_invalid_state"


class SubscriptionInactiveError(AccessDeniedError):
    """The company's subscription has expired or was cancelled: business data is closed."""

    code = "subscription_inactive"


class FeatureNotInPlanError(AccessDeniedError):
    """The module is not switched on for this company's subscription."""

    code = "feature_not_in_plan"


class PlanLimitReachedError(AccessDeniedError):
    """Creating one more record would exceed the company's plan limit."""

    code = "plan_limit_reached"


def validate_limit_overrides(overrides: Mapping[str, int]) -> dict[str, int]:
    """Known metrics only; a limit is a whole number >= 0, or -1 for unlimited."""
    clean: dict[str, int] = {}
    for metric, value in overrides.items():
        if metric not in LIMIT_METRICS:
            raise DomainValidationError(f"Unknown limit '{metric}'.")
        if isinstance(value, bool) or not isinstance(value, int) or value < UNLIMITED:
            raise DomainValidationError("A limit is a whole number, 0 or more, or -1 for none.")
        clean[metric] = value
    return clean


class AccessState(StrEnum):
    """What the subscription currently allows, as seen by the company."""

    ACTIVE = "active"
    EXPIRING = "expiring"  # active, ends within EXPIRY_WARNING_DAYS
    EXPIRED = "expired"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"


# A subscription ending sooner than this is shown as "expiring soon".
EXPIRY_WARNING_DAYS = 14


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
class SubscriptionAssigned(DomainEvent):
    event_type: ClassVar[str] = "subscriptions.subscription_assigned"
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
    # None = no module restriction (subscriptions created before the module catalog).
    features: frozenset[Feature] | None = None
    version: int = 1
    # Per-company limits set by the platform administrator; None = the plan's own limits.
    limit_overrides: dict[str, int] | None = None

    def effective_limits(self, plan: Plan) -> dict[str, int]:
        """The plan's limits with this company's overrides on top (UNLIMITED = no cap)."""
        limits = {metric: plan.limit_for(metric) for metric in LIMIT_METRICS}
        limits.update(self.limit_overrides or {})
        return limits

    def set_limits(self, overrides: Mapping[str, int] | None) -> None:
        """Replace the company's overrides; None or empty returns to the plan's limits."""
        clean = validate_limit_overrides(overrides or {})
        self.limit_overrides = clean or None
        self.version += 1

    @classmethod
    def start(
        cls, *, organization_id: UUID, plan_code: PlanCode = PlanCode.FREE
    ) -> tuple[Subscription, SubscriptionStarted]:
        subscription = cls(id=uuid4(), organization_id=organization_id, plan_code=plan_code)
        return subscription, SubscriptionStarted(
            organization_id=organization_id, plan_code=plan_code.value
        )

    @classmethod
    def assigned(
        cls,
        *,
        organization_id: UUID,
        plan_code: PlanCode,
        started_at: datetime,
        period_end: datetime | None,
        features: frozenset[Feature],
    ) -> tuple[Subscription, SubscriptionAssigned]:
        """A subscription set up by the platform administrator for a new company."""
        _check_period(started_at, period_end)
        subscription = cls(
            id=uuid4(),
            organization_id=organization_id,
            plan_code=plan_code,
            started_at=started_at,
            current_period_end=period_end,
            features=features,
        )
        return subscription, SubscriptionAssigned(
            organization_id=organization_id, plan_code=plan_code.value
        )

    def assign(
        self,
        *,
        plan_code: PlanCode,
        started_at: datetime,
        period_end: datetime | None,
        features: frozenset[Feature],
        status: SubscriptionStatus,
    ) -> SubscriptionAssigned:
        """The platform administrator sets plan, term, modules and status at once.

        Not a payment: nothing here says the company has paid for the term.
        """
        _check_period(started_at, period_end)
        self.plan_code = plan_code
        self.started_at = started_at
        self.current_period_end = period_end
        self.features = features
        if status is SubscriptionStatus.CANCELLED and self.status is not status:
            self.cancelled_at = datetime.now(UTC)
        elif status is not SubscriptionStatus.CANCELLED:
            self.cancelled_at = None
        self.status = status
        self.version += 1
        return SubscriptionAssigned(organization_id=self.organization_id, plan_code=plan_code.value)

    def access_state(self, now: datetime) -> AccessState:
        if self.status is SubscriptionStatus.CANCELLED:
            return AccessState.CANCELLED
        if self.current_period_end is not None and self.current_period_end <= now:
            return AccessState.EXPIRED
        if self.status is SubscriptionStatus.PAST_DUE:
            return AccessState.PAST_DUE
        if (
            self.current_period_end is not None
            and (self.current_period_end - now).days < EXPIRY_WARNING_DAYS
        ):
            return AccessState.EXPIRING
        return AccessState.ACTIVE

    def check_access(self, feature: Feature | None, now: datetime) -> None:
        """Raise unless the company may use ``feature`` (None = the core CRM) right now.

        A past-due subscription keeps working: the platform decides when to cut access.
        """
        if self.access_state(now) in (AccessState.EXPIRED, AccessState.CANCELLED):
            raise SubscriptionInactiveError(
                "The company's subscription is not active. Contact the platform administrator."
            )
        if feature is not None and self.features is not None and feature not in self.features:
            raise FeatureNotInPlanError("This module is not included in the company's plan.")

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


def _check_period(started_at: datetime, period_end: datetime | None) -> None:
    if period_end is not None and period_end <= started_at:
        raise DomainValidationError("The subscription must end after it starts.")
