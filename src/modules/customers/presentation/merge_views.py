"""Merging duplicate customers (spec 2.10): preview what moves, then merge on confirmation."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from modules.customers.application.merge import MergePlan
from modules.customers.composition import customer_merger
from modules.customers.presentation.serializers import customer_to_dict
from modules.customers.presentation.views import CustomerBaseView
from shared.domain.errors import DomainValidationError

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant", str, OpenApiParameter.HEADER, required=True, description="Company slug."
)
_SOURCE = OpenApiParameter(
    "source_id", str, OpenApiParameter.QUERY, required=True, description="The duplicate card."
)


class CustomerMergeSerializer(serializers.Serializer[dict[str, Any]]):
    source_id = serializers.UUIDField()


def _plan(plan: MergePlan) -> dict[str, Any]:
    return {
        "target": customer_to_dict(plan.target),
        "source": customer_to_dict(plan.source),
        "references": plan.references,
        "fills": list(plan.fills),
    }


class CustomerMergeView(CustomerBaseView):
    @extend_schema(
        tags=["Customers"],
        operation_id="customers_merge_preview",
        summary="Preview merging a duplicate into this customer",
        description=(
            "What would move from the duplicate (`source_id`) to this customer: counts of "
            "deals, timeline activities, meetings, calls, payments, invoices, converted leads "
            "and conversations, and which empty contact fields would be filled. Changes nothing."
        ),
        parameters=[_TENANT_HEADER, _SOURCE],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request, customer_id: UUID) -> Response:
        try:
            source_id = UUID(request.query_params.get("source_id", ""))
        except ValueError as exc:
            raise DomainValidationError("source_id must be a customer id.") from exc
        return Response(_plan(customer_merger().preview(self.principal(), source_id, customer_id)))

    @extend_schema(
        tags=["Customers"],
        operation_id="customers_merge",
        summary="Merge a duplicate into this customer",
        description=(
            "Moves everything from the duplicate to this customer in one transaction, fills this "
            "customer's empty contacts from the duplicate, and archives the duplicate with a "
            "`merged_into_id` marker (never deleted). Both cards get an audit entry. Director: "
            "any customers; head of sales: customers of their department; others 403."
        ),
        parameters=[_TENANT_HEADER],
        request=CustomerMergeSerializer,
        responses=OpenApiTypes.OBJECT,
    )
    def post(self, request: Request, customer_id: UUID) -> Response:
        serializer = CustomerMergeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = customer_merger().merge(
            self.principal(), serializer.validated_data["source_id"], customer_id
        )
        return Response(_plan(plan))
