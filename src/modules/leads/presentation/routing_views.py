"""Lead routing settings API: GET/PUT /api/v1/leads/routing/."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from modules.leads.application.routing import RoutingSettings, UpdateRouting
from modules.leads.composition import lead_routing_service
from modules.leads.presentation.views import _TENANT_HEADER, LeadBaseView
from modules.organizations.composition import member_display_name
from shared.domain.errors import DomainValidationError

_DEPARTMENT = OpenApiParameter(
    "department_id",
    str,
    OpenApiParameter.QUERY,
    description="Department to configure (director: required; head of sales: own, may omit).",
)
_TAGS = ["Leads"]


class RoutingCandidateSerializer(serializers.Serializer[dict[str, Any]]):
    user_id = serializers.UUIDField()
    full_name = serializers.CharField(allow_null=True)
    selected = serializers.BooleanField()
    on_shift = serializers.BooleanField()
    absent = serializers.BooleanField()
    eligible = serializers.BooleanField()


class RoutingSettingsSerializer(serializers.Serializer[dict[str, Any]]):
    department_id = serializers.UUIDField()
    enabled = serializers.BooleanField()
    member_ids = serializers.ListField(child=serializers.UUIDField())
    require_on_shift = serializers.BooleanField()
    skip_on_absence = serializers.BooleanField()
    is_company_intake = serializers.BooleanField()
    last_assigned_user_id = serializers.UUIDField(allow_null=True)
    updated_by = serializers.UUIDField(allow_null=True)
    updated_at = serializers.DateTimeField(allow_null=True)
    is_default = serializers.BooleanField()
    candidates = RoutingCandidateSerializer(many=True)


class RoutingUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    enabled = serializers.BooleanField()
    member_ids = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)
    require_on_shift = serializers.BooleanField(required=False, default=True)
    skip_on_absence = serializers.BooleanField(required=False, default=True)
    is_company_intake = serializers.BooleanField(required=False, allow_null=True, default=None)


def _department(request: Request) -> UUID | None:
    raw = request.query_params.get("department_id")
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError as exc:
        raise DomainValidationError("department_id must be a UUID.") from exc


def _to_dict(settings: RoutingSettings, tenant_id: UUID) -> dict[str, Any]:
    rule = settings.rule
    return {
        "department_id": str(rule.department_id),
        "enabled": rule.enabled,
        "member_ids": sorted(str(m) for m in rule.member_ids),
        "require_on_shift": rule.require_on_shift,
        "skip_on_absence": rule.skip_on_absence,
        "is_company_intake": rule.is_company_intake,
        "last_assigned_user_id": (
            str(rule.last_assigned_user_id) if rule.last_assigned_user_id else None
        ),
        "updated_by": str(rule.updated_by) if rule.updated_by else None,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
        "is_default": settings.is_default,
        "candidates": [
            {
                "user_id": str(c.user_id),
                "full_name": member_display_name(tenant_id, c.user_id),
                "selected": c.selected,
                "on_shift": c.on_shift,
                "absent": c.absent,
                "eligible": c.eligible(rule),
            }
            for c in settings.candidates
        ],
    }


class LeadRoutingView(LeadBaseView):
    @extend_schema(
        tags=_TAGS,
        operation_id="leads_routing_get",
        summary="Automatic lead distribution settings of a department",
        description=(
            "Round-robin among the department's active sales managers (lead callers never "
            "take part; optionally only `member_ids`) who are on shift — always required, "
            "`require_on_shift: false` is refused — and not on a planned absence today "
            "(`skip_on_absence: false` is refused too). Off until turned on; the head of sales "
            "may turn it off (`enabled`). `candidates` shows who would be eligible right now. "
            "Director: any department; head of sales: own."
        ),
        parameters=[_TENANT_HEADER, _DEPARTMENT],
        responses=RoutingSettingsSerializer,
    )
    def get(self, request: Request) -> Response:
        settings = lead_routing_service().get(self.principal(), _department(request))
        return Response(_to_dict(settings, self.membership.tenant_id))

    @extend_schema(
        tags=_TAGS,
        operation_id="leads_routing_set",
        summary="Set automatic lead distribution of a department",
        description=(
            "Leads captured by a non-manager (director, head of sales, imports) of the "
            "department go in turn to an eligible manager; leads of members without a "
            "department go to the intake department (`is_company_intake`, director only). "
            "Nobody eligible: the lead stays with its creator and the heads are notified."
        ),
        parameters=[_TENANT_HEADER, _DEPARTMENT],
        request=RoutingUpdateSerializer,
        responses=RoutingSettingsSerializer,
    )
    def put(self, request: Request) -> Response:
        serializer = RoutingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        principal = self.principal()
        department_id = _department(request) or principal.department_id
        if department_id is None:
            raise DomainValidationError("department_id is required.")
        settings = lead_routing_service().save(
            principal,
            UpdateRouting(
                department_id=department_id,
                enabled=data["enabled"],
                member_ids=frozenset(data["member_ids"]),
                require_on_shift=data["require_on_shift"],
                skip_on_absence=data["skip_on_absence"],
                is_company_intake=data["is_company_intake"],
            ),
        )
        return Response(_to_dict(settings, self.membership.tenant_id))
