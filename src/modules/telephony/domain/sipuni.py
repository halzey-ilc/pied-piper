"""Sipuni cloud PBX: its call events, request signatures and a company's connection.

Pure Python. Each company connects its own Sipuni account (doc.sipuni.com, section API):
Sipuni sends call events to a per-company CRM address, and the CRM signs its own requests
(recordings, click-to-call) with the company's integration key.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from modules.telephony.domain.call import CallDirection, CallStatus
from shared.domain.errors import DomainValidationError

LANGUAGES = ("ru", "ky")
CHANNELS = ("mono", "manager_left", "manager_right")
_EXTENSION_RE = re.compile(r"^\d{2,6}$")
_ACCOUNT_RE = re.compile(r"^\d{3,12}$")
MAX_EXTENSIONS = 500


class EventKind(StrEnum):
    CALL = "1"
    HANGUP = "2"
    ANSWER = "3"
    SECONDARY_HANGUP = "4"


# Sipuni's hang-up status -> the CRM's terminal call status.
HANGUP_STATUS = {
    "ANSWER": CallStatus.COMPLETED,
    "BUSY": CallStatus.MISSED,
    "NOANSWER": CallStatus.MISSED,
    "CANCEL": CallStatus.MISSED,
    "CONGESTION": CallStatus.FAILED,
    "CHANUNAVAIL": CallStatus.FAILED,
}

EXTERNAL, INTERNAL = 1, 2


@dataclass(frozen=True, slots=True)
class SipuniEvent:
    kind: EventKind
    call_id: str
    src_num: str
    src_type: int
    dst_num: str
    dst_type: int
    timestamp: int
    status: str | None = None
    started: int | None = None
    answered: int | None = None
    record_link: str | None = None
    short_src: str | None = None
    short_dst: str | None = None
    inner: bool = False


def _int(params: Mapping[str, str], name: str, *, required: bool = True) -> int | None:
    raw = (params.get(name) or "").strip()
    if not raw:
        if required:
            raise DomainValidationError(f"Sipuni event without {name}.")
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise DomainValidationError(f"Sipuni event field {name} is not a number.") from exc


def parse_event(params: Mapping[str, str]) -> SipuniEvent:
    """One event from Sipuni's GET or POST parameters ("Отправка событий HTTP")."""
    try:
        kind = EventKind((params.get("event") or "").strip())
    except ValueError as exc:
        raise DomainValidationError("Unknown Sipuni event.") from exc
    call_id = (params.get("call_id") or "").strip()
    if not call_id or len(call_id) > 255:
        raise DomainValidationError("Sipuni event without a call id.")
    return SipuniEvent(
        kind=kind,
        call_id=call_id,
        src_num=(params.get("src_num") or "").strip(),
        src_type=_int(params, "src_type") or 0,
        dst_num=(params.get("dst_num") or "").strip(),
        dst_type=_int(params, "dst_type") or 0,
        timestamp=_int(params, "timestamp") or 0,
        status=(params.get("status") or "").strip().upper() or None,
        started=_int(params, "call_start_timestamp", required=False),
        answered=_int(params, "call_answer_timestamp", required=False),
        record_link=(params.get("call_record_link") or "").strip() or None,
        short_src=(params.get("short_src_num") or "").strip() or None,
        short_dst=(params.get("short_dst_num") or "").strip() or None,
        inner=(params.get("is_inner_call") or "").strip() == "1",
    )


def sign(values: Sequence[str], secret: str) -> str:
    """Sipuni's request hash: MD5 of the values joined with "+" in the documented order,
    the integration key last."""
    return hashlib.md5("+".join([*values, secret]).encode(), usedforsecurity=False).hexdigest()


def normalize_phone(raw: str, country_code: str) -> str | None:
    """E.164 from what a PBX reports: 996555123456, 0555123456, 555123456."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 11:
        return f"+{digits}"
    if len(digits) == 10 and digits.startswith("0"):
        return f"+{country_code}{digits[1:]}"
    if len(digits) == 9:
        return f"+{country_code}{digits}"
    return None


@dataclass(slots=True)
class SipuniConnection:
    """A company's Sipuni account as the CRM keeps it. ``secret`` is the integration key,
    stored encrypted; the webhook token is kept only as a hash."""

    account: str  # Sipuni cabinet number ("user" in its API)
    secret: str
    company_number: str  # the company's line in E.164, the CRM side of every call
    fallback_owner_id: UUID  # owns calls of an extension nobody is mapped to
    webhook_token_hash: str
    extensions: dict[str, UUID] = field(default_factory=dict)  # short number -> user
    country_code: str = "996"
    call_language: str = "ru"  # what the calls are mostly in
    record_channels: str = "mono"  # how Sipuni records: mono or stereo with the manager left/right
    analyze_calls: bool = True  # transcribe and review answered calls
    enabled: bool = True
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not _ACCOUNT_RE.match(self.account):
            raise DomainValidationError("The Sipuni account number must be digits.")
        if not self.secret.strip():
            raise DomainValidationError("The Sipuni integration key is required.")
        if normalize_phone(self.company_number, self.country_code) is None:
            raise DomainValidationError("The company number must be a phone number.")
        self.company_number = normalize_phone(self.company_number, self.country_code) or ""
        if self.call_language not in LANGUAGES:
            raise DomainValidationError("Call language must be ru or ky.")
        if self.record_channels not in CHANNELS:
            raise DomainValidationError(
                "Recording channels must be mono, manager_left or manager_right."
            )
        if len(self.extensions) > MAX_EXTENSIONS or not all(
            _EXTENSION_RE.match(e) for e in self.extensions
        ):
            raise DomainValidationError("Internal numbers must be 2-6 digits.")

    def short_number(self, login: str, short: str | None) -> str | None:
        """The internal number: Sipuni sends it short, or as a login <account><short>."""
        if short:
            return short
        if login.startswith(self.account) and len(login) > len(self.account):
            return login[len(self.account) :]
        return login or None

    def owner_for(self, short: str | None) -> UUID:
        return self.extensions.get(short or "", self.fallback_owner_id)

    def extension_for(self, user_id: UUID) -> str | None:
        return next((short for short, uid in self.extensions.items() if uid == user_id), None)


@dataclass(frozen=True, slots=True)
class ExternalCall:
    """A finished call as the CRM logs it."""

    external_id: str
    direction: CallDirection
    customer_phone: str
    company_phone: str
    owner_user_id: UUID
    status: CallStatus
    started_at: datetime
    duration_seconds: int
    recording_url: str | None


def finished_call(event: SipuniEvent, connection: SipuniConnection) -> ExternalCall | None:
    """The call a hang-up event finishes; None for internal calls and numbers that are
    not phone numbers (nothing to log for a customer)."""
    if event.kind is not EventKind.HANGUP or event.inner:
        return None
    if event.src_type == EXTERNAL and event.dst_type != EXTERNAL:
        direction, customer, short = (
            CallDirection.INBOUND,
            event.src_num,
            connection.short_number(event.dst_num, event.short_dst),
        )
    elif event.dst_type == EXTERNAL and event.src_type != EXTERNAL:
        direction, customer, short = (
            CallDirection.OUTBOUND,
            event.dst_num,
            connection.short_number(event.src_num, event.short_src),
        )
    else:
        return None
    phone = normalize_phone(customer, connection.country_code)
    if phone is None:
        return None
    status = HANGUP_STATUS.get(event.status or "", CallStatus.FAILED)
    started = event.started or event.timestamp
    answered = event.answered or 0
    duration = max(0, event.timestamp - answered) if answered > 0 else 0
    return ExternalCall(
        external_id=f"sipuni:{event.call_id}",
        direction=direction,
        customer_phone=phone,
        company_phone=connection.company_number,
        owner_user_id=connection.owner_for(short),
        status=status if answered > 0 or status is not CallStatus.COMPLETED else CallStatus.MISSED,
        started_at=datetime.fromtimestamp(started, tz=UTC),
        duration_seconds=min(duration, 24 * 60 * 60),
        recording_url=event.record_link if answered > 0 else None,
    )
