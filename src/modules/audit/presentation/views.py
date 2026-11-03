from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from modules.access_control.domain.rbac import Principal, Role
from modules.audit.application.journal import JournalEntry, JournalFilter
from modules.audit.composition import audit_journal_service
from modules.organizations.composition import member_display_name
from platform_core.presentation.tenancy import TenantScopedAPIView
from shared.domain.errors import DomainValidationError

_TAGS = ["Audit"]
MAX_PAGE_SIZE = 200


def _q(name: str, kind: Any, description: str) -> OpenApiParameter:
    return OpenApiParameter(name, kind, OpenApiParameter.QUERY, description=description)


_PARAMETERS = [
    OpenApiParameter(
        "X-Tenant", str, OpenApiParameter.HEADER, required=True, description="Company slug."
    ),
    _q("actor_id", str, "Only actions of this user."),
    _q("resource_type", str, "e.g. deal, lead, customer, membership, plan."),
    _q("resource_id", str, "One record's entries."),
    _q("action", str, "Exact action (`deal.won`) or a prefix ending with a dot (`deal.`)."),
    _q("date_from", OpenApiTypes.DATE, "From this day (UTC), inclusive."),
    _q("date_to", OpenApiTypes.DATE, "To this day (UTC), inclusive."),
    _q("limit", int, "Page size 1-200 (default 50)."),
    _q("offset", int, "Rows to skip."),
]


def _uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise DomainValidationError("actor_id must be a UUID.") from exc


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DomainValidationError("Dates must be YYYY-MM-DD.") from exc


class AuditJournalView(TenantScopedAPIView):
    def principal(self) -> Principal:
        return Principal(
            user_id=self.membership.user_id,
            tenant_id=self.membership.tenant_id,
            role=Role(self.membership.role),
            team_ids=self.membership.team_ids,
            department_id=self.membership.department_id,
        )

    @extend_schema(
        tags=_TAGS,
        operation_id="audit_journal",
        summary="Journal of user actions",
        description=(
            "Audited actions in the company, newest first: who, when, what record and which "
            "fields changed (names only, never values; `details` may hold identifiers such as "
            "the previous and the new owner). The director sees the whole company, a head of "
            "sales only actions of their department's members; others get 403. Read-only."
        ),
        parameters=_PARAMETERS,
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request: Request) -> Response:
        q = request.query_params
        try:
            limit = min(max(int(q.get("limit", 50)), 1), MAX_PAGE_SIZE)
            offset = max(int(q.get("offset", 0)), 0)
        except ValueError:
            limit, offset = 50, 0
        filters = JournalFilter(
            actor_user_id=_uuid(q.get("actor_id")),
            resource_type=(q.get("resource_type") or "").strip()[:100] or None,
            resource_id=(q.get("resource_id") or "").strip()[:64] or None,
            action=(q.get("action") or "").strip()[:100] or None,
            date_from=_date(q.get("date_from")),
            date_to=_date(q.get("date_to")),
        )
        entries, total = audit_journal_service().entries(
            self.principal(), filters, limit=limit, offset=offset
        )
        names: dict[UUID, str | None] = {}
        tenant_id = self.membership.tenant_id
        for entry in entries:
            if entry.actor_user_id is not None and entry.actor_user_id not in names:
                names[entry.actor_user_id] = member_display_name(tenant_id, entry.actor_user_id)
        return Response({"count": total, "results": [_to_dict(e, names) for e in entries]})


def _to_dict(entry: JournalEntry, names: dict[UUID, str | None]) -> dict[str, Any]:
    actor = entry.actor_user_id
    return {
        "id": str(entry.id),
        "occurred_at": entry.occurred_at.isoformat(),
        "actor": {"id": str(actor), "name": names.get(actor)} if actor else None,
        "action": entry.action,
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_id,
        "changed_fields": list(entry.changed_fields),
        "details": entry.details,
    }
