"""Sipuni adapters: the encrypted connection store, the HTTP client and the ports the
event handling needs. The integration key and the recording links are never logged."""

from __future__ import annotations

import base64
import hashlib
from uuid import UUID, uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from modules.leads.composition import lead_service
from modules.leads.domain.lead import LeadSource
from modules.organizations.composition import active_member_departments
from modules.telephony.domain.call import CallLink, SubjectType
from modules.telephony.domain.sipuni import SipuniConnection, sign
from modules.telephony.infrastructure.models import TelephonyConnectionRecord
from shared.application.retry import PermanentError, TransientError
from shared.domain.errors import DomainValidationError

API_URL = "https://sipuni.com/api"
MAX_RECORDING_BYTES = 50 * 1024 * 1024
_SINGLETON = 1
# Stands for a stored key the server can no longer decrypt (not a credential).
UNREADABLE_SECRET = "-"  # noqa: S105


def _box() -> Fernet:
    # Its own key derivation (not the MFA one): rotating one secret must not affect the other.
    digest = hashlib.sha256(f"telephony:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class DjangoConnectionStore:
    def __init__(self, using: str) -> None:
        self.using = using

    def get(self) -> SipuniConnection | None:
        row = TelephonyConnectionRecord.objects.using(self.using).filter(id=_SINGLETON).first()
        if row is None:
            return None
        try:
            secret = _box().decrypt(bytes(row.encrypted_secret)).decode()
        except InvalidToken:
            # The server's secret key changed: the director has to enter the key again.
            secret = UNREADABLE_SECRET
        return SipuniConnection(
            account=row.account,
            secret=secret,
            company_number=row.company_number,
            fallback_owner_id=row.fallback_owner_id,
            webhook_token_hash=row.webhook_token_hash,
            extensions={k: UUID(v) for k, v in row.extensions.items()},
            country_code=row.country_code,
            call_language=row.call_language,
            record_channels=row.record_channels,
            analyze_calls=row.analyze_calls,
            enabled=row.enabled,
            updated_at=row.updated_at,
        )

    def save(self, connection: SipuniConnection) -> None:
        TelephonyConnectionRecord.objects.using(self.using).update_or_create(
            id=_SINGLETON,
            defaults={
                "provider": "sipuni",
                "account": connection.account,
                "encrypted_secret": _box().encrypt(connection.secret.encode()),
                "company_number": connection.company_number,
                "fallback_owner_id": connection.fallback_owner_id,
                "webhook_token_hash": connection.webhook_token_hash,
                "extensions": {k: str(v) for k, v in connection.extensions.items()},
                "country_code": connection.country_code,
                "call_language": connection.call_language,
                "record_channels": connection.record_channels,
                "analyze_calls": connection.analyze_calls,
                "enabled": connection.enabled,
            },
        )

    def delete(self) -> None:
        TelephonyConnectionRecord.objects.using(self.using).filter(id=_SINGLETON).delete()


class SipuniClient:
    """Requests the CRM makes to Sipuni, signed with the company's integration key."""

    def __init__(
        self,
        connection: SipuniConnection,
        *,
        base_url: str = API_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._c = connection
        self._http = httpx.Client(
            base_url=base_url, timeout=timeout, transport=transport, follow_redirects=True
        )

    def _post(self, path: str, data: dict[str, str]) -> httpx.Response:
        try:
            response = self._http.post(path, data=data)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientError(f"Sipuni unreachable: {type(exc).__name__}") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise TransientError(f"Sipuni returned {response.status_code}")
        if response.status_code >= 400:
            raise PermanentError(f"Sipuni rejected the request: {response.status_code}")
        return response

    def call_number(self, *, sipnumber: str, phone: str) -> str:
        """Click-to-call: ring the manager's internal number first, then the customer.
        Hash order per the docs: antiaon, phone, reverse, sipnumber, user, secret."""
        digits = "".join(ch for ch in phone if ch.isdigit())
        values = {"antiaon": "0", "phone": digits, "reverse": "0", "sipnumber": sipnumber}
        data = {
            **values,
            # The docs name the flag both ways; the hash uses the value either way.
            "phoneantiaon": "0",
            "user": self._c.account,
            "hash": sign([*values.values(), self._c.account], self._c.secret),
        }
        response = self._post("/callback/call_number", data)
        try:
            body = response.json()
        except ValueError:
            body = {}
        # The docs promise "the call order id" without naming the field.
        found = next(
            (body[k] for k in ("id", "callId", "data") if isinstance(body, dict) and k in body),
            None,
        )
        order = found if isinstance(found, str | int) else uuid4().hex[:16]
        return f"sipuni-order:{order}"

    def recording(self, link: str) -> tuple[bytes, str]:
        """The audio behind a call's record link (from the hang-up event)."""
        try:
            with self._http.stream("GET", link) as response:
                if response.status_code >= 500:
                    raise TransientError(f"Sipuni returned {response.status_code}")
                if response.status_code >= 400:
                    raise PermanentError(f"Sipuni refused the recording: {response.status_code}")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RECORDING_BYTES:
                        raise PermanentError("The recording is larger than 50 MB.")
                    chunks.append(chunk)
                content_type = response.headers.get("content-type", "audio/mpeg").split(";")[0]
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientError(f"Sipuni unreachable: {type(exc).__name__}") from exc
        if not content_type.startswith("audio/"):
            content_type = "audio/mpeg"  # Sipuni serves mp3 (doc example: <id>.mp3)
        return b"".join(chunks), content_type


class CustomerMatcher:
    """A customer with this phone, through the leads module's duplicate search."""

    def link_for(self, phone: str) -> CallLink | None:
        try:
            # No owner: an unknown number is matched, never captured as a lead here.
            match = lead_service().match_or_capture_contact(
                phone=phone, name=None, source=LeadSource.PHONE, owner_user_id=None
            )
        except DomainValidationError:
            return None
        if match.customer_id is None:
            return None
        return CallLink(type=SubjectType.CUSTOMER, id=match.customer_id)


class MemberDepartments:
    def __init__(self, tenant_id: UUID) -> None:
        self._tenant_id = tenant_id

    def department_of(self, user_id: UUID) -> UUID | None:
        return next(
            (d for u, _, d in active_member_departments(self._tenant_id) if u == user_id), None
        )


class SipuniDialer:
    """Click-to-call through Sipuni when the company is connected and the manager has an
    internal number; otherwise the stub (no real call), as before."""

    def __init__(self, store: DjangoConnectionStore, fallback: object) -> None:
        self._store = store
        self._fallback = fallback

    def dial(self, *, from_number: str, to_number: str, owner_user_id: UUID | None = None) -> str:
        connection = self._store.get()
        extension = (
            connection.extension_for(owner_user_id) if connection and owner_user_id else None
        )
        if connection is None or not connection.enabled or extension is None:
            return self._fallback.dial(from_number=from_number, to_number=to_number)  # type: ignore[attr-defined,no-any-return]
        return SipuniClient(connection).call_number(sipnumber=extension, phone=to_number)
