from __future__ import annotations

from typing import Any

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.subscriptions.application.service import SubscriptionView
from modules.subscriptions.composition import subscription_service
from modules.subscriptions.domain.plan import PlanCode, all_plans
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`. Selects the organization.",
)
_TAGS = ["Subscription"]


class ChangePlanSerializer(serializers.Serializer[dict[str, Any]]):
    plan_code = serializers.ChoiceField(choices=[p.value for p in PlanCode])


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
        )


def _view_to_dict(view: SubscriptionView) -> dict[str, Any]:
    sub, plan = view.subscription, view.plan
    return {
        "organization_id": str(sub.organization_id),
        "status": sub.status.value,
        "plan": {
            "code": plan.code.value,
            "name": plan.name,
            "monthly_price": str(plan.monthly_price),
            "currency": plan.currency,
            "limits": plan.limits,
        },
        "started_at": sub.started_at.isoformat(),
        "current_period_end": sub.current_period_end.isoformat()
        if sub.current_period_end
        else None,
        "cancelled_at": sub.cancelled_at.isoformat() if sub.cancelled_at else None,
        "version": sub.version,
    }


class PlanListView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="plans_list",
        summary="List available plans",
        description="The plan catalog with prices and limits (-1 means unlimited).",
        parameters=[_TENANT_HEADER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        self.principal()  # ensure membership / tenant resolved
        return Response(
            {
                "plans": [
                    {
                        "code": p.code.value,
                        "name": p.name,
                        "monthly_price": str(p.monthly_price),
                        "currency": p.currency,
                        "limits": p.limits,
                    }
                    for p in all_plans()
                ]
            }
        )


class CurrentSubscriptionView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="subscription_current",
        summary="Get the current subscription",
        description=(
            "Returns this organization's subscription (creating a Free one on first access) "
            "with its plan and limits. Requires the subscriptions.view permission."
        ),
        parameters=[_TENANT_HEADER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        return Response(_view_to_dict(subscription_service().current(self.principal())))

    @extend_schema(
        tags=_TAGS,
        operation_id="subscription_change_plan",
        summary="Change the plan",
        description="Switches the organization to another plan. Requires subscriptions.manage.",
        parameters=[_TENANT_HEADER],
        request=ChangePlanSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request) -> Response:
        serializer = ChangePlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        view = subscription_service().change_plan(
            self.principal(), PlanCode(serializer.validated_data["plan_code"])
        )
        return Response(_view_to_dict(view))


class SubscriptionCancelView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="subscription_cancel",
        summary="Cancel the subscription",
        description="Cancels the organization's subscription. Requires subscriptions.manage.",
        parameters=[_TENANT_HEADER],
        request=None,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request) -> Response:
        return Response(_view_to_dict(subscription_service().cancel(self.principal())))


class SubscriptionReactivateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="subscription_reactivate",
        summary="Reactivate the subscription",
        description=(
            "Reactivates a cancelled or past-due subscription. Requires subscriptions.manage."
        ),
        parameters=[_TENANT_HEADER],
        request=None,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request) -> Response:
        return Response(_view_to_dict(subscription_service().reactivate(self.principal())))
