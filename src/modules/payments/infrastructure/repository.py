from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import F, Q, QuerySet

from modules.payments.application.ports import InvoiceFilter, InvoiceVisibility
from modules.payments.domain.invoice import Invoice, InvoiceStatus
from modules.payments.infrastructure.models import InvoiceRecord
from shared.domain.errors import ConflictError
from shared.domain.value_objects import Money


def _to_domain(row: InvoiceRecord) -> Invoice:
    return Invoice(
        id=row.id,
        number=row.number,
        total=Money(row.total_amount, row.currency),
        paid=Money(row.paid_amount, row.currency),
        owner_user_id=row.owner_user_id,
        status=InvoiceStatus(row.status),
        customer_id=row.customer_id,
        deal_id=row.deal_id,
        team_id=row.team_id,
        due_date=row.due_date,
        issued_at=row.issued_at,
        paid_at=row.paid_at,
        created_at=row.created_at,
        version=row.version,
    )


class DjangoInvoiceRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[InvoiceRecord]:
        return InvoiceRecord.objects.using(self.using)

    def _filtered(
        self, visibility: InvoiceVisibility, filters: InvoiceFilter
    ) -> QuerySet[InvoiceRecord]:
        qs = self._qs()
        if visibility.owner_user_id is not None:
            condition = Q(owner_user_id=visibility.owner_user_id)
            if visibility.team_ids:
                condition |= Q(team_id__in=visibility.team_ids)
            qs = qs.filter(condition)
        if filters.status is not None:
            qs = qs.filter(status=filters.status.value)
        if filters.customer_id is not None:
            qs = qs.filter(customer_id=filters.customer_id)
        if filters.deal_id is not None:
            qs = qs.filter(deal_id=filters.deal_id)
        return qs

    def add(self, invoice: Invoice) -> None:
        try:
            with transaction.atomic(using=self.using):
                self._qs().create(
                    id=invoice.id, created_at=invoice.created_at, **self._fields(invoice)
                )
        except IntegrityError:
            raise ConflictError("An invoice with this number already exists.") from None

    def get(self, invoice_id: UUID) -> Invoice | None:
        row = self._qs().filter(id=invoice_id).first()
        return None if row is None else _to_domain(row)

    def save(self, invoice: Invoice, expected_version: int) -> None:
        updated = (
            self._qs()
            .filter(id=invoice.id, version=expected_version)
            .update(version=F("version") + 1, **self._fields(invoice))
        )
        if updated != 1:
            raise ConflictError("Invoice was modified by someone else; reload and retry.")

    def list_visible(
        self, visibility: InvoiceVisibility, filters: InvoiceFilter, *, limit: int, offset: int
    ) -> list[Invoice]:
        rows = self._filtered(visibility, filters).order_by("-created_at", "id")[
            offset : offset + limit
        ]
        return [_to_domain(row) for row in rows]

    def count(self, visibility: InvoiceVisibility, filters: InvoiceFilter) -> int:
        return self._filtered(visibility, filters).count()

    def _fields(self, invoice: Invoice) -> dict[str, Any]:
        return {
            "number": invoice.number,
            "total_amount": invoice.total.amount,
            "paid_amount": invoice.paid.amount,
            "currency": invoice.total.currency,
            "status": invoice.status.value,
            "customer_id": invoice.customer_id,
            "deal_id": invoice.deal_id,
            "owner_user_id": invoice.owner_user_id,
            "team_id": invoice.team_id,
            "due_date": invoice.due_date,
            "issued_at": invoice.issued_at,
            "paid_at": invoice.paid_at,
        }
