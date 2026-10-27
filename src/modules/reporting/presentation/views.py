from __future__ import annotations

from uuid import UUID

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.reporting.composition import report_service
from modules.reporting.domain.report import ReportFormat, ReportRequest, ReportType
from modules.reporting.presentation.serializers import (
    ReportJobSerializer,
    ReportRequestSerializer,
    report_job_to_dict,
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
_TAGS = ["Analytics"]


class _Scoped(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
        )


class ReportCreateView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="reports_request",
        summary="Request a report",
        description=(
            "Queues an asynchronous report and returns the job immediately (202). The report "
            "is rendered by a worker, not in this request. Poll the job and download when its "
            "status is `succeeded`. Idempotent on `idempotency_key`: repeating a request "
            "returns the same job (200). Requires the reports.generate permission."
        ),
        parameters=[_TENANT_HEADER],
        request=ReportRequestSerializer,
        responses={201: ReportJobSerializer, 200: ReportJobSerializer},
        examples=[
            OpenApiExample(
                "KPI summary",
                value={
                    "report_type": "kpi_summary",
                    "period_start": "2026-10-01",
                    "period_end": "2026-10-31",
                    "idempotency_key": "kpi-2026-10",
                },
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = ReportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        report_request = ReportRequest(
            report_type=ReportType(data["report_type"]),
            period_start=data["period_start"],
            period_end=data["period_end"],
            fmt=ReportFormat(data["fmt"]),
            idempotency_key=data["idempotency_key"],
        )
        job, created = report_service().request_report(self.principal(), report_request)
        return Response(
            report_job_to_dict(job),
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )


class ReportDetailView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="reports_status",
        summary="Report job status",
        description="Returns the status of a report job. Download when `status` is `succeeded`.",
        parameters=[_TENANT_HEADER],
        responses=ReportJobSerializer,
    )
    def get(self, request: Request, job_id: UUID) -> Response:
        return Response(report_job_to_dict(report_service().get_job(self.principal(), job_id)))


class ReportDownloadView(_Scoped):
    @extend_schema(
        tags=_TAGS,
        operation_id="reports_download",
        summary="Download a finished report",
        description="Streams the rendered file. 404 until the report is ready.",
        parameters=[_TENANT_HEADER],
        responses={(200, "text/csv"): OpenApiTypes.BINARY},
    )
    def get(self, request: Request, job_id: UUID) -> HttpResponse:
        content, content_type, filename = report_service().download(self.principal(), job_id)
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
