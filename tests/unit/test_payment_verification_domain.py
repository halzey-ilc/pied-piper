"""Payment receipts and verification (owner decision of 03.11.2026). Pure domain."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from modules.payments.domain.payment import (
    Payment,
    PaymentAlreadyVerifiedError,
    PaymentKind,
    PaymentStatus,
    VerificationMethod,
)
from modules.payments.domain.receipt import (
    MAX_RECEIPT_BYTES,
    Receipt,
    ReceiptRejectedError,
    detect_receipt_type,
)
from shared.domain.errors import DomainValidationError
from shared.domain.value_objects import Money


def _payment(**extra: object) -> Payment:
    return Payment.register(
        amount=Money(Decimal("100.00"), "KGS"),
        kind=PaymentKind.PREPAYMENT,
        paid_on=date(2026, 11, 3),
        seller_user_id=uuid4(),
        owner_user_id=uuid4(),
        deal_id=uuid4(),
        **extra,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"%PDF-1.7\n...", "application/pdf"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "image/png"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00", "image/heic"),
        (b"MZ\x90\x00 an executable", None),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", None),
        (b"GIF89a", None),
    ],
)
def test_receipt_type_comes_from_magic_bytes(content: bytes, expected: str | None) -> None:
    assert detect_receipt_type(content) == expected


def test_receipt_rejects_disguised_empty_and_oversized_files() -> None:
    user = uuid4()
    with pytest.raises(ReceiptRejectedError):
        Receipt.upload(
            content=b"<html>not a pdf</html>", filename="check.pdf", uploaded_by_user_id=user
        )
    with pytest.raises(ReceiptRejectedError):
        Receipt.upload(content=b"", filename="check.pdf", uploaded_by_user_id=user)
    with pytest.raises(ReceiptRejectedError):
        Receipt.upload(
            content=b"%PDF-" + b"0" * MAX_RECEIPT_BYTES,
            filename="big.pdf",
            uploaded_by_user_id=user,
        )


def test_receipt_keeps_a_safe_display_name_and_checksum() -> None:
    receipt = Receipt.upload(
        content=b"%PDF-1.4 ok", filename='C:\\fakepath\\../Чек "1".pdf', uploaded_by_user_id=uuid4()
    )
    assert receipt.filename == "Чек 1.pdf"
    assert receipt.content_type == "application/pdf" and receipt.size == 11
    assert len(receipt.sha256) == 64


def test_a_new_payment_waits_for_verification_and_a_refund_does_not() -> None:
    payment = _payment()
    assert payment.status is PaymentStatus.PENDING_VERIFICATION and not payment.counts
    refund = _payment(is_refund=True)
    assert refund.status is PaymentStatus.CONFIRMED and refund.counts
    assert refund.verification_method is VerificationMethod.MANUAL


def test_confirm_and_reject_are_one_time_decisions() -> None:
    payment = _payment()
    verifier = uuid4()
    event = payment.confirm(verifier_user_id=verifier, method=VerificationMethod.MANUAL)
    assert payment.counts and payment.verified_by_user_id == verifier
    assert event.payment_id == payment.id
    with pytest.raises(PaymentAlreadyVerifiedError):
        payment.reject(verifier_user_id=verifier, reason="late")

    rejected = _payment()
    with pytest.raises(DomainValidationError):
        rejected.reject(verifier_user_id=verifier, reason="   ")
    rejected.reject(verifier_user_id=verifier, reason="  Amount   differs ")
    assert rejected.status is PaymentStatus.REJECTED and not rejected.counts
    assert rejected.rejection_reason == "Amount differs"
    with pytest.raises(PaymentAlreadyVerifiedError):
        rejected.confirm(verifier_user_id=verifier, method=VerificationMethod.MANUAL)


def test_only_a_payment_on_a_deal_closes_it_as_a_sale() -> None:
    with pytest.raises(DomainValidationError):
        _payment(is_refund=True, close_sale=True)
    assert _payment(close_sale=True).close_sale
