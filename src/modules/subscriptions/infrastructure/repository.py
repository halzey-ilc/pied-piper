from __future__ import annotations

from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import F

from modules.subscriptions.domain.plan import PlanCode
from modules.subscriptions.domain.subscription import Subscription, SubscriptionStatus
from modules.subscriptions.infrastructure.models import SubscriptionRecord
from shared.domain.errors import ConflictError

# Control-plane module: always the default (global) database.
_USING = "default"


def _to_domain(row: SubscriptionRecord) -> Subscription:
    return Subscription(
        id=row.id,
        organization_id=row.organization_id,
        plan_code=PlanCode(row.plan_code),
        status=SubscriptionStatus(row.status),
        started_at=row.started_at,
        current_period_end=row.current_period_end,
        cancelled_at=row.cancelled_at,
        version=row.version,
    )


class DjangoSubscriptionRepository:
    def get_by_org(self, organization_id: UUID) -> Subscription | None:
        row = (
            SubscriptionRecord.objects.using(_USING).filter(organization_id=organization_id).first()
        )
        return None if row is None else _to_domain(row)

    def add(self, subscription: Subscription) -> None:
        try:
            with transaction.atomic(using=_USING):
                SubscriptionRecord.objects.using(_USING).create(
                    id=subscription.id,
                    organization_id=subscription.organization_id,
                    plan_code=subscription.plan_code.value,
                    status=subscription.status.value,
                    started_at=subscription.started_at,
                    current_period_end=subscription.current_period_end,
                    cancelled_at=subscription.cancelled_at,
                )
        except IntegrityError:
            raise ConflictError("A subscription already exists for this organization.") from None

    def save(self, subscription: Subscription, expected_version: int) -> None:
        updated = (
            SubscriptionRecord.objects.using(_USING)
            .filter(id=subscription.id, version=expected_version)
            .update(
                plan_code=subscription.plan_code.value,
                status=subscription.status.value,
                current_period_end=subscription.current_period_end,
                cancelled_at=subscription.cancelled_at,
                version=F("version") + 1,
            )
        )
        if updated != 1:
            raise ConflictError("Subscription was modified by someone else; reload and retry.")
