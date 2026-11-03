"""Automatic receipt verification adapters.

The adapter slot: an implementation reads the receipt (PDF text layer, OCR of a photo, a
bank or payment-provider API) and answers whether it proves the payment — amount, date
and recipient — with ``ReceiptCheck.MATCHED`` (the payment is confirmed automatically,
method ``auto``) or ``MISMATCH`` / ``NEEDS_MANUAL_REVIEW`` (the head of sales or the
director decides). Until a real reader is chosen, every receipt goes to manual review:
nothing is confirmed without a person.
"""

from __future__ import annotations

from modules.payments.application.ports import ReceiptCheck, ReceiptVerdict
from modules.payments.domain.payment import Payment
from modules.payments.domain.receipt import Receipt


class ManualReviewVerifier:
    """No automatic check: every receipt waits for the head of sales or the director."""

    def verify(self, receipt: Receipt, payment: Payment) -> ReceiptVerdict:
        return ReceiptVerdict(
            check=ReceiptCheck.NEEDS_MANUAL_REVIEW,
            note="Automatic receipt reading is not connected; checked manually.",
        )
