"""Records of other modules that point at a customer — moved when duplicates are merged.

A system-level adapter for one use case (merging customer cards, spec 2.10): it only re-points
existing references from one customer to another inside the company's own database. Records
keep their owners, history and audit trail; versioned rows get a new version so concurrent
editors see the change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import F, Model

from modules.activities.infrastructure.models import ActivityRecord
from modules.communications.infrastructure.models import ConversationRecord
from modules.leads.infrastructure.models import LeadRecord
from modules.meetings.infrastructure.models import MeetingRecord
from modules.payments.infrastructure.models import InvoiceRecord, PaymentRecord
from modules.sales_pipeline.infrastructure.models import DealRecord
from modules.telephony.infrastructure.models import CallRecord


@dataclass(frozen=True, slots=True)
class _Reference:
    label: str  # stable key shown in the merge preview
    model: type[Model]
    field: str  # the column holding the customer id
    extra: Callable[[], dict[str, Any]] | None = None  # extra filter (e.g. subject type)
    versioned: bool = False


_REFERENCES: tuple[_Reference, ...] = (
    _Reference("deals", DealRecord, "customer_id", versioned=True),
    _Reference("activities", ActivityRecord, "subject_id", lambda: {"subject_type": "customer"}),
    _Reference("meetings", MeetingRecord, "subject_id", lambda: {"subject_type": "customer"}),
    _Reference("calls", CallRecord, "subject_id", lambda: {"subject_type": "customer"}),
    _Reference("payments", PaymentRecord, "customer_id"),
    _Reference("invoices", InvoiceRecord, "customer_id", versioned=True),
    _Reference("leads", LeadRecord, "converted_customer_id"),
    _Reference("conversations", ConversationRecord, "customer_id"),
)


class DjangoCustomerReferences:
    def __init__(self, using: str) -> None:
        self.using = using

    def _rows(self, ref: _Reference, customer_id: UUID) -> Any:
        manager = ref.model._default_manager.using(self.using)
        return manager.filter(**{ref.field: customer_id, **(ref.extra() if ref.extra else {})})

    def count(self, customer_id: UUID) -> dict[str, int]:
        return {ref.label: self._rows(ref, customer_id).count() for ref in _REFERENCES}

    def move(self, source_id: UUID, target_id: UUID) -> dict[str, int]:
        moved: dict[str, int] = {}
        for ref in _REFERENCES:
            changes: dict[str, Any] = {ref.field: target_id}
            if ref.versioned:
                changes["version"] = F("version") + 1
            moved[ref.label] = self._rows(ref, source_id).update(**changes)
        return moved
