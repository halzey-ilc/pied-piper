from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from modules.payments.domain.invoice import (
    Invoice,
    InvoiceIssued,
    InvoicePaid,
    InvoiceStateError,
)
from shared.domain.errors import DomainValidationError
from shared.domain.value_objects import Money


def _draft(amount: str = "100.00", currency: str = "USD") -> Invoice:
    return Invoice.draft(total=Money(Decimal(amount), currency), owner_user_id=uuid4())


def test_draft_generates_number_and_zero_paid() -> None:
    invoice = _draft()
    assert invoice.status.value == "draft"
    assert invoice.number.startswith("INV-")
    assert invoice.paid.amount == Decimal("0.00")
    assert invoice.outstanding.amount == Decimal("100.00")


def test_total_must_be_positive() -> None:
    with pytest.raises(DomainValidationError):
        Invoice.draft(total=Money(Decimal("0"), "USD"), owner_user_id=uuid4())


def test_issue_then_partial_and_full_payment() -> None:
    invoice = _draft("100.00")
    event = invoice.issue()
    assert isinstance(event, InvoiceIssued) and invoice.status.value == "issued"

    received, paid = invoice.record_payment(Money(Decimal("40"), "USD"))
    assert paid is None and invoice.paid.amount == Decimal("40.00")
    assert invoice.outstanding.amount == Decimal("60.00")
    assert received.amount == "40.00"

    _, paid2 = invoice.record_payment(Money(Decimal("60"), "USD"))
    assert isinstance(paid2, InvoicePaid)
    assert invoice.status.value == "paid" and invoice.paid_at is not None


def test_payment_rules() -> None:
    invoice = _draft("100.00")
    with pytest.raises(InvoiceStateError):  # not issued yet
        invoice.record_payment(Money(Decimal("10"), "USD"))
    invoice.issue()
    with pytest.raises(DomainValidationError):  # wrong currency
        invoice.record_payment(Money(Decimal("10"), "EUR"))
    with pytest.raises(DomainValidationError):  # exceeds outstanding
        invoice.record_payment(Money(Decimal("101"), "USD"))


def test_void_rules() -> None:
    invoice = _draft()
    invoice.void("duplicate")
    assert invoice.status.value == "void"
    with pytest.raises(InvoiceStateError):
        invoice.void("again")

    paid = _draft()
    paid.issue()
    paid.record_payment(Money(Decimal("100"), "USD"))
    with pytest.raises(InvoiceStateError):
        paid.void("x")
