from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from modules.platform_admin.application.ports import (
    CompanyDetail,
    CompanySummary,
    ErrorRecord,
    SubscriptionInfo,
    Ticket,
)
from modules.platform_admin.application.service import Overview
from modules.platform_admin.domain.support import ErrorStatus, TicketKind, TicketStatus
from modules.subscriptions.domain.payment import SubscriptionPayment
from modules.subscriptions.domain.plan import Feature, PlanCode, all_plans
from modules.subscriptions.domain.subscription import AccessState, SubscriptionStatus

_FEATURES = [f.value for f in Feature]


class TermsSerializer(serializers.Serializer[dict[str, Any]]):
    plan_code = serializers.ChoiceField(choices=[p.value for p in PlanCode])
    starts_on = serializers.DateField()
    ends_on = serializers.DateField(
        required=False, allow_null=True, help_text="Last day of access (inclusive)"
    )
    features = serializers.ListField(
        child=serializers.ChoiceField(choices=_FEATURES),
        required=False,
        allow_null=True,
        help_text="Switched-on modules; omitted = the plan's defaults",
    )
    status = serializers.ChoiceField(
        choices=[s.value for s in SubscriptionStatus], required=False, default="active"
    )


class DirectorInputSerializer(serializers.Serializer[dict[str, Any]]):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    login = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.CharField(max_length=254, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True, default="")


class CompanyCreateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=200)
    slug = serializers.CharField(max_length=40, help_text="Short name used as X-Tenant")
    country = serializers.CharField(max_length=2, required=False, default="KG")
    currency = serializers.CharField(max_length=3, required=False, default="KGS")
    timezone = serializers.CharField(max_length=64, required=False, default="Asia/Bishkek")
    subscription = TermsSerializer()
    director = DirectorInputSerializer()


class CompanyUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=200, required=False)
    is_active = serializers.BooleanField(required=False)


class ErrorStatusSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=[s.value for s in ErrorStatus])


class TicketUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.ChoiceField(choices=[s.value for s in TicketStatus], required=False)
    internal_note = serializers.CharField(
        max_length=5000, required=False, allow_blank=True, trim_whitespace=False
    )


class TicketCreateSerializer(serializers.Serializer[dict[str, Any]]):
    kind = serializers.ChoiceField(choices=[k.value for k in TicketKind])
    subject = serializers.CharField(max_length=200)
    body = serializers.CharField(max_length=5000)


def subscription_to_dict(s: SubscriptionInfo | None) -> dict[str, Any] | None:
    if s is None:
        return None
    return {
        "plan_code": s.plan_code.value,
        "status": s.status.value,
        "access_state": s.access_state.value,
        "starts_on": s.starts_on.isoformat(),
        "ends_on": s.ends_on.isoformat() if s.ends_on else None,
        "features": None if s.features is None else sorted(f.value for f in s.features),
    }


def summary_to_dict(c: CompanySummary) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "slug": c.slug,
        "name": c.name,
        "is_active": c.is_active,
        "created_at": c.created_at.isoformat(),
        "tenant_status": c.tenant_status,
        "users": c.users,
        "subscription": subscription_to_dict(c.subscription),
        "staff": None
        if c.staff is None
        else {
            "employees": c.staff.employees,
            "leaders": c.staff.leaders,
            "departments": c.staff.departments,
            "heads_of_sales": c.staff.heads_of_sales,
            "managers": c.staff.managers,
        },
    }


def detail_to_dict(d: CompanyDetail) -> dict[str, Any]:
    return {
        **summary_to_dict(d.summary),
        "country": d.country,
        "currency": d.currency,
        "timezone": d.timezone,
        "departments": d.departments,
        "directors": [
            {
                "user_id": str(x.user_id),
                "login": x.login,
                "full_name": x.full_name,
                "is_active": x.is_active,
                "status": "deactivated"
                if not x.is_active
                else "invited"
                if x.must_change_password and x.last_login is None
                else "active",
                "last_login": x.last_login.isoformat() if x.last_login else None,
            }
            for x in d.directors
        ],
        "history": [
            {
                "occurred_at": h.occurred_at.isoformat(),
                "action": h.action,
                "actor": h.actor,
                "changed_fields": list(h.changed_fields),
            }
            for h in d.history
        ],
    }


def overview_to_dict(o: Overview) -> dict[str, Any]:
    c = o.counts
    return {
        "period_days": o.period_days,
        "plan": o.plan.value if o.plan is not None else None,
        "companies": {
            "total": c.companies,
            "active": c.active_companies,
            "suspended": c.suspended_companies,
            "new_in_period": c.new_companies,
        },
        "subscriptions": {
            **{state.value: c.access.get(state, 0) for state in AccessState},
            "none": c.without_subscription,
            "giving_access": sum(c.paying_plans.values()),
            "by_plan": {code.value: n for code, n in c.paying_plans.items()},
        },
        "users": {"active": c.users},
        "revenue": {
            # Per catalog plan: subscriptions giving access and their monthly list price sum.
            "by_plan": [
                {
                    "plan": r.plan_code.value,
                    "companies": r.companies,
                    "currency": r.currency,
                    "amount": str(r.monthly),
                }
                for r in o.assigned_by_plan
            ],
            # List prices of the plans that give access now: an assignment, not a payment.
            "assigned_monthly": [
                {"currency": cur, "amount": str(amount)}
                for cur, amount in sorted(o.assigned_monthly.items())
            ],
            # Confirmed subscription payments paid within the period (real money received).
            "received": [
                {"currency": cur, "amount": str(amount)}
                for cur, amount in sorted(o.received.items())
            ],
            "received_unavailable_reason": None,
            # Debt needs issued obligations (invoices); the platform does not issue them yet.
            "overdue": None,
            "overdue_unavailable_reason": (
                "Overdue amounts need issued subscription invoices, which the platform "
                "does not record."
            ),
        },
        "errors": {"new": c.errors_new, "in_period": c.errors_in_period},
        "tickets": {"open": c.tickets_open, "in_period": c.tickets_in_period},
    }


def catalog_to_dict() -> dict[str, Any]:
    return {
        "plans": [
            {
                "code": p.code.value,
                "name": p.name,
                "monthly_price": str(p.monthly_price),
                "currency": p.currency,
                "limits": p.limits,
                "features": sorted(f.value for f in p.features),
                "trial_days": p.trial_days,
            }
            for p in all_plans()
        ],
        "features": _FEATURES,
    }


def error_to_dict(e: ErrorRecord) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "occurred_at": e.occurred_at.isoformat(),
        "component": e.component,
        "view": e.view,
        "method": e.method,
        "route": e.route,
        "exception_type": e.exception_type,
        "request_id": e.request_id or None,
        "tenant_slug": e.tenant_slug or None,
        "status": e.status.value,
        "handled_at": e.handled_at.isoformat() if e.handled_at else None,
    }


def ticket_to_dict(t: Ticket, *, internal: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(t.id),
        "company": {"id": str(t.company_id), "name": t.company_name},
        "author": {"id": str(t.author_id), "name": t.author_name},
        "kind": t.kind.value,
        "subject": t.subject,
        "body": t.body,
        "status": t.status.value,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }
    if internal:
        out["internal_note"] = t.internal_note
    return out


class PaymentRecordSerializer(serializers.Serializer[dict[str, Any]]):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    currency = serializers.CharField(min_length=3, max_length=3)
    paid_on = serializers.DateField()
    reference = serializers.CharField(
        max_length=100, help_text="External operation id: bank reference, receipt number"
    )
    period_start = serializers.DateField(required=False, allow_null=True)
    period_end = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    confirm = serializers.BooleanField(
        required=False, default=False, help_text="true: the money is already confirmed"
    )


class PaymentCancelSerializer(serializers.Serializer[dict[str, Any]]):
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


def payment_to_dict(p: SubscriptionPayment) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "company_id": str(p.organization_id),
        "subscription_id": str(p.subscription_id) if p.subscription_id else None,
        "amount": str(p.amount),
        "currency": p.currency,
        "paid_on": p.paid_on.isoformat(),
        "reference": p.reference,
        "source": p.source.value,
        "status": p.status.value,
        "period_start": p.period_start.isoformat() if p.period_start else None,
        "period_end": p.period_end.isoformat() if p.period_end else None,
        "note": p.note,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "created_at": p.created_at.isoformat(),
    }
