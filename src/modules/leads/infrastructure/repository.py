from __future__ import annotations

import re
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import F, Q, QuerySet

from modules.leads.application.ports import LEAD_ORDERING, LeadFilter, LeadVisibility
from modules.leads.domain.lead import Lead, LeadSource, LeadStatus
from modules.leads.infrastructure.models import LeadRecord
from shared.domain.errors import ConflictError
from shared.domain.value_objects import EmailAddress, PhoneNumber


def _to_domain(row: LeadRecord) -> Lead:
    return Lead(
        id=row.id,
        name=row.name,
        owner_user_id=row.owner_user_id,
        source=LeadSource(row.source),
        status=LeadStatus(row.status),
        email=EmailAddress(row.email) if row.email else None,
        phone=PhoneNumber(row.phone) if row.phone else None,
        team_id=row.team_id,
        disqualify_reason=row.disqualify_reason,
        converted_customer_id=row.converted_customer_id,
        created_at=row.created_at,
        version=row.version,
    )


class DjangoLeadRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[LeadRecord]:
        return LeadRecord.objects.using(self.using)

    def _visible(self, visibility: LeadVisibility) -> QuerySet[LeadRecord]:
        qs = self._qs()
        if visibility.owner_user_id is None:
            return qs
        condition = Q(owner_user_id=visibility.owner_user_id)
        if visibility.team_ids:
            condition |= Q(team_id__in=visibility.team_ids)
        return qs.filter(condition)

    def add(self, lead: Lead) -> None:
        try:
            with transaction.atomic(using=self.using):
                self._qs().create(
                    id=lead.id,
                    name=lead.name,
                    email=str(lead.email) if lead.email else None,
                    phone=str(lead.phone) if lead.phone else None,
                    source=lead.source.value,
                    status=lead.status.value,
                    owner_user_id=lead.owner_user_id,
                    team_id=lead.team_id,
                    disqualify_reason=lead.disqualify_reason,
                    converted_customer_id=lead.converted_customer_id,
                    version=lead.version,
                    created_at=lead.created_at,
                )
        except IntegrityError:  # pragma: no cover - no unique business constraints yet
            raise ConflictError("Could not create the lead.") from None

    def get(self, lead_id: UUID) -> Lead | None:
        row = self._qs().filter(id=lead_id).first()
        return None if row is None else _to_domain(row)

    def save(self, lead: Lead, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=lead.id, version=expected_version)
            .update(
                name=lead.name,
                email=str(lead.email) if lead.email else None,
                phone=str(lead.phone) if lead.phone else None,
                status=lead.status.value,
                owner_user_id=lead.owner_user_id,
                team_id=lead.team_id,
                disqualify_reason=lead.disqualify_reason,
                converted_customer_id=lead.converted_customer_id,
                version=F("version") + 1,
            )
        )
        if updated != 1:
            raise ConflictError("Lead was modified by someone else; reload and retry.")

    def _filtered(self, visibility: LeadVisibility, filters: LeadFilter) -> QuerySet[LeadRecord]:
        qs = self._visible(visibility)
        if filters.status is not None:
            qs = qs.filter(status=filters.status.value)
        if filters.owner_user_id is not None:
            qs = qs.filter(owner_user_id=filters.owner_user_id)
        if filters.source is not None:
            qs = qs.filter(source=filters.source.value)
        if filters.created_since is not None:
            qs = qs.filter(created_at__gte=filters.created_since)
        if filters.created_until is not None:
            qs = qs.filter(created_at__lt=filters.created_until)
        if filters.search:
            condition = Q(name__icontains=filters.search) | Q(email__icontains=filters.search)
            digits = re.sub(r"\D", "", filters.search)
            if digits:
                condition |= Q(phone__icontains=digits)
            qs = qs.filter(condition)
        return qs

    def list_visible(
        self, visibility: LeadVisibility, filters: LeadFilter, *, limit: int, offset: int
    ) -> list[Lead]:
        ordering = filters.ordering or "-created_at"
        if ordering.lstrip("-") not in LEAD_ORDERING:
            ordering = "-created_at"
        rows = self._filtered(visibility, filters).order_by(ordering, "id")[offset : offset + limit]
        return [_to_domain(row) for row in rows]

    def count(self, visibility: LeadVisibility, filters: LeadFilter) -> int:
        return self._filtered(visibility, filters).count()
