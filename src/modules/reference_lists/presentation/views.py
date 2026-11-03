from __future__ import annotations

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.reference_lists.composition import reference_list_service
from modules.reference_lists.domain.lists import parse_key
from modules.reference_lists.presentation.serializers import (
    ItemCreateSerializer,
    ItemReadSerializer,
    ItemUpdateSerializer,
    ListReadSerializer,
    ReorderSerializer,
    item_to_dict,
    list_to_dict,
)
from platform_core.presentation.tenancy import TenantScopedAPIView

_TENANT_HEADER = OpenApiParameter(
    "X-Tenant",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Short name (slug) of your company, e.g. `demo`.",
    examples=[OpenApiExample("Demo tenant", value="demo")],
)
_ACTIVE = OpenApiParameter(
    "active", bool, OpenApiParameter.QUERY, description="true: only items offered in forms."
)
_TAGS = ["Reference lists"]
_KEYS = "`meeting_cancel`, `meeting_no_show` or `lead_source`"


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


class ReferenceListView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="reference_list_get",
        summary="A company reference list",
        description=(
            f"Items of a list ({_KEYS}) in order. Any member of the company can read it; "
            "`editable` says whether the caller may change it (company.settings.manage — the "
            "director). Lead sources are the fixed codes of the leads API with the company's "
            "labels; switched-off sources are hidden from forms (`active=true`)."
        ),
        parameters=[_TENANT_HEADER, _ACTIVE],
        responses=ListReadSerializer,
    )
    def get(self, request: Request, key: str) -> Response:
        list_key = parse_key(key)
        service = reference_list_service()
        active_only = request.query_params.get("active") in {"1", "true", "True"}
        items = service.items(list_key, active_only=active_only)
        editable = service.can_manage(self.principal())
        return Response(list_to_dict(list_key, items, editable=editable))

    @extend_schema(
        tags=_TAGS,
        operation_id="reference_list_add",
        summary="Add an item to a list",
        description="Meeting reason lists only (lead sources are fixed). Director only.",
        parameters=[_TENANT_HEADER],
        request=ItemCreateSerializer,
        responses={201: ItemReadSerializer},
    )
    def post(self, request: Request, key: str) -> Response:
        serializer = ItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = reference_list_service().add(
            self.principal(), parse_key(key), serializer.validated_data["label"]
        )
        return Response(item_to_dict(item), status=status.HTTP_201_CREATED)


class ReferenceItemView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="reference_list_update_item",
        summary="Rename or switch an item on/off",
        description=(
            "`item` is the item id for meeting reasons and the source code for lead sources. "
            "Items are switched off, never deleted, so past choices stay readable. Director only."
        ),
        parameters=[_TENANT_HEADER],
        request=ItemUpdateSerializer,
        responses=ItemReadSerializer,
    )
    def patch(self, request: Request, key: str, item: str) -> Response:
        serializer = ItemUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updated = reference_list_service().update(
            self.principal(),
            parse_key(key),
            item,
            label=data.get("label"),
            is_active=data.get("is_active"),
        )
        return Response(item_to_dict(updated))


class ReferenceOrderView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="reference_list_reorder",
        summary="Reorder a meeting reason list",
        description="Send every item id once, in the new order. Director only.",
        parameters=[_TENANT_HEADER],
        request=ReorderSerializer,
        responses=ListReadSerializer,
    )
    def put(self, request: Request, key: str) -> Response:
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        list_key = parse_key(key)
        service = reference_list_service()
        items = service.reorder(self.principal(), list_key, serializer.validated_data["ids"])
        return Response(list_to_dict(list_key, items, editable=True))
