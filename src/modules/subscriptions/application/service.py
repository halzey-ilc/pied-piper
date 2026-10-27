"""Subscription use cases (control plane). RBAC enforced here.

Each operation acts on the caller's own organization (``principal.tenant_id``);
owners/admins manage it, everyone else may only view.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.subscriptions.application.ports import SubscriptionRepository
from modules.subscriptions.domain.plan import Plan, PlanCode, get_plan
from modules.subscriptions.domain.subscription import Subscription
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.events import DomainEvent

AGGREGATE = "subscription"


@dataclass(frozen=True, slots=True)
class SubscriptionView:
    subscription: Subscription
    plan: Plan


class SubscriptionService:
    def __init__(
        self,
        repository: SubscriptionRepository,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def current(self, principal: Principal) -> SubscriptionView:
        """Return the organization's subscription, creating a Free one on first access."""
        self._policy.require(principal, Permission.SUBSCRIPTIONS_VIEW)
        subscription = self._repo.get_by_org(principal.tenant_id)
        if subscription is None:
            with self._uow:
                subscription, event = Subscription.start(organization_id=principal.tenant_id)
                self._repo.add(subscription)
                self._emit(event, subscription)
                self._record(principal, "subscription.started", subscription)
        return SubscriptionView(subscription, get_plan(subscription.plan_code))

    def change_plan(self, principal: Principal, plan_code: PlanCode) -> SubscriptionView:
        get_plan(plan_code)  # validate the plan exists
        return self._manage(
            principal, lambda sub: [sub.change_plan(plan_code)], "subscription.plan_changed"
        )

    def cancel(self, principal: Principal) -> SubscriptionView:
        return self._manage(principal, lambda sub: [sub.cancel()], "subscription.cancelled")

    def reactivate(self, principal: Principal) -> SubscriptionView:
        def action(sub: Subscription) -> list[DomainEvent]:
            sub.reactivate()
            return []

        return self._manage(principal, action, "subscription.reactivated")

    def _manage(self, principal: Principal, action: _Action, audit_action: str) -> SubscriptionView:
        self._policy.require(principal, Permission.SUBSCRIPTIONS_MANAGE)
        view = self.current(principal)
        with self._uow:
            subscription = view.subscription
            version = subscription.version
            events = action(subscription)
            self._repo.save(subscription, expected_version=version)
            for event in events:
                self._emit(event, subscription)
            self._record(principal, audit_action, subscription)
        return SubscriptionView(subscription, get_plan(subscription.plan_code))

    def _emit(self, event: DomainEvent, subscription: Subscription) -> None:
        self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(subscription.id))

    def _record(self, principal: Principal, action: str, subscription: Subscription) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(subscription.id),
            )
        )


_Action = Callable[[Subscription], list[DomainEvent]]
