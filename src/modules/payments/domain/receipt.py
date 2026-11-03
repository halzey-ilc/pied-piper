"""Payment receipts: the cheque or bank receipt a seller uploads with a payment. Pure Python.

Owner decision of 03.11.2026: every payment carries its receipt (PDF or an image) and a
sale counts only after the payment is confirmed. The file type is decided by its first
bytes, never by the name or the browser's declared type.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError

MAX_RECEIPT_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_FILENAME_LENGTH = 255

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"
HEIC = "image/heic"

ALLOWED_RECEIPT_TYPES = (PDF, JPEG, PNG, WEBP, HEIC)

# ISO-BMFF brands of HEIC/HEIF photos (iPhone camera default).
_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


class ReceiptRejectedError(DomainValidationError):
    """The file is empty, too large or not a PDF/JPEG/PNG/WEBP/HEIC."""

    code = "receipt_invalid"


class ReceiptRequiredError(DomainValidationError):
    """A payment (not a refund) is recorded only with its receipt."""

    code = "receipt_required"


def detect_receipt_type(content: bytes) -> str | None:
    """The receipt's media type from its magic bytes, or None if it is not allowed."""
    if content.startswith(b"%PDF-"):
        return PDF
    if content.startswith(b"\xff\xd8\xff"):
        return JPEG
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return WEBP
    if len(content) >= 12 and content[4:8] == b"ftyp" and content[8:12] in _HEIC_BRANDS:
        return HEIC
    return None


def _clean_filename(filename: str) -> str:
    # Keep only the last path segment and printable characters; the name is shown, never
    # used as a path.
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '"<>').strip()
    if not name:
        name = "receipt"
    return name[:MAX_FILENAME_LENGTH]


@dataclass(slots=True)
class Receipt:
    id: UUID
    content: bytes = field(repr=False)
    content_type: str
    size: int
    filename: str
    sha256: str
    uploaded_by_user_id: UUID
    uploaded_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def upload(cls, *, content: bytes, filename: str, uploaded_by_user_id: UUID) -> Receipt:
        if not content:
            raise ReceiptRejectedError("The receipt file is empty.")
        if len(content) > MAX_RECEIPT_BYTES:
            raise ReceiptRejectedError("The receipt must be at most 10 MB.")
        content_type = detect_receipt_type(content)
        if content_type is None:
            raise ReceiptRejectedError("The receipt must be a PDF, JPEG, PNG, WEBP or HEIC file.")
        return cls(
            id=uuid4(),
            content=content,
            content_type=content_type,
            size=len(content),
            filename=_clean_filename(filename),
            sha256=hashlib.sha256(content).hexdigest(),
            uploaded_by_user_id=uploaded_by_user_id,
        )


@dataclass(frozen=True, slots=True)
class ReceiptInfo:
    """Receipt metadata shown with a payment (the file itself is streamed separately)."""

    id: UUID
    content_type: str
    size: int
    filename: str
    sha256: str
    uploaded_by_user_id: UUID
    uploaded_at: datetime
