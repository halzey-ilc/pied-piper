"""DuplicateFinder adapter: matches a lead's e-mail or phone against other leads
and existing customers in the active tenant database (read-only, no merge)."""

from __future__ import annotations

from uuid import UUID

from django.db.models import Q

from modules.customers.infrastructure.models import CustomerRecord
from modules.leads.application.ports import DuplicateCandidate
from modules.leads.infrastructure.models import LeadRecord

_MAX_CANDIDATES = 50


def _match(email: str | None, phone_digits: str | None) -> Q:
    condition = Q()
    if email:
        condition |= Q(email__iexact=email)
    if phone_digits:
        condition |= Q(phone__icontains=phone_digits)
    return condition


class DjangoDuplicateFinder:
    def __init__(self, using: str) -> None:
        self.using = using

    def find(
        self, *, exclude_lead_id: UUID, email: str | None, phone_digits: str | None
    ) -> list[DuplicateCandidate]:
        condition = _match(email, phone_digits)
        if not condition:
            return []
        candidates: list[DuplicateCandidate] = []
        lead_rows = (
            LeadRecord.objects.using(self.using)
            .filter(condition)
            .exclude(id=exclude_lead_id)
            .order_by("-created_at")[:_MAX_CANDIDATES]
        )
        candidates.extend(
            DuplicateCandidate(
                kind="lead",
                id=row.id,
                name=row.name,
                email=row.email,
                phone=row.phone,
                status=row.status,
            )
            for row in lead_rows
        )
        customer_rows = (
            CustomerRecord.objects.using(self.using)
            .filter(condition)
            .order_by("-created_at")[:_MAX_CANDIDATES]
        )
        candidates.extend(
            DuplicateCandidate(
                kind="customer",
                id=row.id,
                name=row.name,
                email=row.email,
                phone=row.phone,
            )
            for row in customer_rows
        )
        return candidates
