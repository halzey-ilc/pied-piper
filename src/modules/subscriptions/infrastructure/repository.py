from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import F, Sum

from modules.subscriptions.application.ports import PaymentFilter
from modules.subscriptions.domain.payment import (
    DuplicatePaymentError,
    PaymentSource,
    PaymentStateError,
    PaymentStatus,
    SubscriptionPayment,
)
from modules.subscriptions.domain.plan import Feature, PlanCode
from modules.subscriptions.domain.subscription import Subscription, SubscriptionStatus
from modules.subscriptions.infrastructure.models import (
    SubscriptionPaymentRecord,
    SubscriptionRecord,
)
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
        features=None if row.features is None else _features(row.features),
        version=row.version,
    )


def _features(values: list[str]) -> frozenset[Feature]:
    # Unknown values (a module removed from the catalog) are dropped, not fatal.
    return frozenset(Feature(v) for v in values if v in Feature.__members__.values())


def _stored(features: frozenset[Feature] | None) -> list[str] | None:
    return None if features is None else sorted(f.value for f in features)


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
                    features=_stored(subscription.features),
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
                started_at=subscription.started_at,
                features=_stored(subscription.features),
                current_period_end=subscription.current_period_end,
                cancelled_at=subscription.cancelled_at,
                version=F("version") + 1,
            )
        )
        if updated != 1:
            raise ConflictError("Subscription was modified by someone else; reload and retry.")


def _payment(row: SubscriptionPaymentRecord) -> SubscriptionPayment:
    return SubscriptionPayment(
        id=row.id,
        organization_id=row.organization_id,
        subscription_id=row.subscription_id,
        amount=row.amount,
        currency=row.currency,
        paid_on=row.paid_on,
        reference=row.reference,
        source=PaymentSource(row.source),
        status=PaymentStatus(row.status),
        recorded_by=row.recorded_by,
        period_start=row.period_start,
        period_end=row.period_end,
        note=row.note,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        created_at=row.created_at,
    )


class DjangoSubscriptionPaymentRepository:
    def add(self, payment: SubscriptionPayment) -> None:
        try:
            with transaction.atomic(using=_USING):
                SubscriptionPaymentRecord.objects.using(_USING).create(
                    id=payment.id,
                    organization_id=payment.organization_id,
                    subscription_id=payment.subscription_id,
                    amount=payment.amount,
                    currency=payment.currency,
                    paid_on=payment.paid_on,
                    reference=payment.reference,
                    source=payment.source.value,
                    status=payment.status.value,
                    period_start=payment.period_start,
                    period_end=payment.period_end,
                    note=payment.note,
                    recorded_by=payment.recorded_by,
                    created_at=payment.created_at,
                )
        except IntegrityError:
            raise DuplicatePaymentError(
                "A payment with this reference is already recorded."
            ) from None

    def get(self, payment_id: UUID) -> SubscriptionPayment | None:
        row = SubscriptionPaymentRecord.objects.using(_USING).filter(id=payment_id).first()
        return None if row is None else _payment(row)

    def save_decision(self, payment: SubscriptionPayment) -> None:
        updated = (
            SubscriptionPaymentRecord.objects.using(_USING)
            .filter(id=payment.id, status=PaymentStatus.PENDING.value)
            .update(
                status=payment.status.value,
                decided_by=payment.decided_by,
                decided_at=payment.decided_at,
                note=payment.note,
            )
        )
        if updated != 1:
            raise PaymentStateError("The payment has already been decided.")

    def list(
        self, filters: PaymentFilter, *, limit: int, offset: int
    ) -> tuple[list[SubscriptionPayment], int]:
        qs = SubscriptionPaymentRecord.objects.using(_USING).order_by("-paid_on", "-created_at")
        if filters.organization_id is not None:
            qs = qs.filter(organization_id=filters.organization_id)
        if filters.status is not None:
            qs = qs.filter(status=filters.status.value)
        if filters.paid_from is not None:
            qs = qs.filter(paid_on__gte=filters.paid_from)
        if filters.paid_to is not None:
            qs = qs.filter(paid_on__lte=filters.paid_to)
        return [_payment(row) for row in qs[offset : offset + limit]], qs.count()

    def confirmed_totals(self, paid_from: date, paid_to: date) -> dict[str, Decimal]:
        rows = (
            SubscriptionPaymentRecord.objects.using(_USING)
            .filter(
                status=PaymentStatus.CONFIRMED.value, paid_on__gte=paid_from, paid_on__lte=paid_to
            )
            .values("currency")
            .annotate(total=Sum("amount"))
        )
        return {row["currency"]: row["total"] for row in rows}
