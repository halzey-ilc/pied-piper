"""Recording payments through the API the way the product does: with a receipt, then
verified by someone other than the seller (owner decision of 03.11.2026)."""

from __future__ import annotations

from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

# The smallest files the receipt sniffer accepts (magic bytes only matter).
PDF_RECEIPT = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"
PNG_RECEIPT = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe"
    b"\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)


def receipt_file(content: bytes = PDF_RECEIPT, name: str = "check.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="application/octet-stream")


def post_payment(client: APIClient, receipt: SimpleUploadedFile | None = None, **body: Any) -> Any:
    """POST /payments/ as multipart with a receipt (a refund may go without one)."""
    body.setdefault("kind", "prepayment")
    body.setdefault("paid_on", "2026-11-10")
    data: dict[str, Any] = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in body.items()
        if value is not None
    }
    if receipt is None and not body.get("is_refund"):
        receipt = receipt_file()
    if receipt is not None:
        data["receipt"] = receipt
    return client.post("/api/v1/payments/", data, format="multipart")


def pay_confirmed(client: APIClient, verifier: APIClient, **body: Any) -> dict[str, Any]:
    """Record a payment with its receipt and have ``verifier`` confirm it."""
    created = post_payment(client, **body)
    assert created.status_code == 201, created.content
    payment: dict[str, Any] = created.json()
    if payment["status"] == "confirmed":  # a refund
        return payment
    confirmed = verifier.post(f"/api/v1/payments/{payment['id']}/confirm/", {}, format="json")
    assert confirmed.status_code == 200, confirmed.content
    result: dict[str, Any] = confirmed.json()
    return result
