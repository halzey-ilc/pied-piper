from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.imports.composition import import_service
from modules.imports.domain.rows import ImportEntity
from modules.imports.presentation.serializers import (
    ImportRequestSerializer,
    job_to_dict,
    preview_to_dict,
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
_TAGS = ["Imports"]
_DEALS_EXAMPLE = OpenApiExample(
    "Deals from a spreadsheet",
    value={
        "entity": "deals",
        "filename": "deals.csv",
        "content": (
            "Сделка;Сумма;Этап;Ответственный;Клиент;Телефон;Следующий шаг;Срок шага\n"
            "Поставка 10 шт;120000;Квалификация;aibek;ОсОО Альфа;+996555123456;"  # noqa: RUF001
            "Позвонить;2026-11-10\n"
        ),
    },
)
_DEALS_HELP = (
    " Deals (`entity=deals`): columns Сделка/Title (required), Сумма/Amount, Валюта/Currency "
    "(default: the company currency), Воронка/Pipeline (default: your department's default "
    "pipeline, else the company one), Этап/Stage (default: the first open stage), "
    "Ответственный/Owner (login or e-mail of an active member; default: `owner_user_id` or "
    "you; a head of sales only within their department), Клиент + Телефон/E-mail (an "
    "existing customer with that phone or e-mail is linked, else one is created), Дата "
    "закрытия, Источник, Следующий шаг + Срок шага, Причина проигрыша. Rows on a won stage "
    "(«Продажа») or on «Встреча перенесена» are `invalid` (a sale needs a confirmed payment; "
    "a reschedule needs a meeting time); a lost stage needs a loss reason; another later "
    "open stage needs a next step. A deal is a `duplicate` when the company already has a "
    "deal with that title for the same customer. Each row has `deal` with what will be "
    "created (pipeline, stage, owner, customer match or creation)."
)
_EXAMPLE = OpenApiExample(
    "Leads from a spreadsheet",
    value={
        "entity": "leads",
        "filename": "leads.csv",
        "content": "Имя;Телефон;E-mail;Источник\nАйгуль;+996555123456;;сайт\n",  # noqa: RUF001
    },
)


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )


class ImportPreviewView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="imports_preview",
        summary="Check a CSV file before importing",
        description=(
            "Parses a CSV (comma, semicolon or tab; header row with Имя/Name, Телефон/Phone, "
            "E-mail, Источник/Source) and classifies every row: `new`, `duplicate` (the phone "
            "or e-mail already belongs to a lead or customer of the company, or repeats an "
            "earlier row) or `invalid` (with the reason). Nothing is saved. Phones must be "
            "international (+996…). Up to 2000 rows / 2 MB." + _DEALS_HELP
        ),
        parameters=[_TENANT_HEADER],
        request=ImportRequestSerializer,
        responses=OpenApiTypes.OBJECT,
        examples=[_EXAMPLE, _DEALS_EXAMPLE],
    )
    def post(self, request: Request) -> Response:
        s = ImportRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        preview = import_service().preview(
            self.principal(),
            ImportEntity(s.validated_data["entity"]),
            s.validated_data["content"],
            s.validated_data.get("owner_user_id"),
        )
        return Response(preview_to_dict(preview))


class ImportListCreateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="imports_list",
        summary="Recent imports",
        description="The company's last 20 imports with their counts and row errors.",
        parameters=[_TENANT_HEADER],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        jobs = import_service().recent(self.principal())
        return Response({"count": len(jobs), "results": [job_to_dict(j) for j in jobs]})

    @extend_schema(
        tags=_TAGS,
        operation_id="imports_create",
        summary="Import leads, customers or deals from a CSV file",
        description=(
            "Creates the rows the preview marks as `new`, one by one through the lead, "
            "customer or deal service (same rules, events and audit as manual creation); "
            "duplicates and invalid rows are skipped and reported. `owner_user_id` hands every "
            "created record to that member (only within your area of responsibility); "
            "default: you. A deal row is opened on the first stage, handed to its owner and "
            "moved to its stage in one transaction (a refused step creates nothing for the "
            "row). Requires leads.create / customers.create / deals.create. The import is "
            "audited as `import.completed`."
        ),
        parameters=[_TENANT_HEADER],
        request=ImportRequestSerializer,
        responses={201: OpenApiTypes.OBJECT},
        examples=[_EXAMPLE, _DEALS_EXAMPLE],
    )
    def post(self, request: Request) -> Response:
        s = ImportRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        job = import_service().commit(
            self.principal(),
            ImportEntity(data["entity"]),
            data.get("filename") or "import.csv",
            data["content"],
            data.get("owner_user_id"),
        )
        return Response(job_to_dict(job), status=status.HTTP_201_CREATED)
