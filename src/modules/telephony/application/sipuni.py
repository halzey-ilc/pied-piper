"""Sipuni connection and call events (a company's own Sipuni account).

* A director (integrations.manage) connects the account: the CRM stores the integration
  key encrypted and gives an event address with a random token, shown once.
* Sipuni sends call events there. A hang-up becomes a logged call, linked to the
  customer or lead with that phone and owned by the manager of the internal number;
  telephony.call_ended lets the AI module analyse answered, recorded calls.
* Click-to-call goes through Sipuni when the manager has an internal number.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.telephony.application.ports import CallRepository
from modules.telephony.domain.call import Call, CallDirection, CallLink
from modules.telephony.domain.sipuni import (
    SipuniConnection,
    finished_call,
    parse_event,
)
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.domain.errors import DomainValidationError, NotFoundError

logger = logging.getLogger(__name__)

AGGREGATE = "call"
CONNECTION = "telephony_connection"


class ConnectionStore(Protocol):
    def get(self) -> SipuniConnection | None: ...

    def save(self, connection: SipuniConnection) -> None: ...

    def delete(self) -> None: ...


class ContactMatcher(Protocol):
    def link_for(self, phone: str) -> CallLink | None:
        """The customer with this phone, if any (system path, no principal)."""
        ...


class Departments(Protocol):
    def department_of(self, user_id: UUID) -> UUID | None: ...


@dataclass(frozen=True, slots=True)
class SaveConnection:
    account: str
    secret: str | None  # None keeps the stored key
    company_number: str
    extensions: dict[str, UUID]
    country_code: str = "996"
    call_language: str = "ru"
    record_channels: str = "mono"
    analyze_calls: bool = True
    enabled: bool = True
    rotate_token: bool = False


@dataclass(frozen=True, slots=True)
class SavedConnection:
    connection: SipuniConnection
    # The event address token, only when it was just created: it is not stored readable.
    webhook_token: str | None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SipuniService:
    def __init__(
        self,
        store: ConnectionStore,
        calls: CallRepository,
        contacts: ContactMatcher,
        departments: Departments,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._store = store
        self._calls = calls
        self._contacts = contacts
        self._departments = departments
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._policy = policy or AccessPolicy()

    # --- connection (director) ---------------------------------------------------

    def connection(self, principal: Principal) -> SipuniConnection | None:
        self._policy.require(principal, Permission.INTEGRATIONS_MANAGE)
        return self._store.get()

    def save(self, principal: Principal, command: SaveConnection) -> SavedConnection:
        self._policy.require(principal, Permission.INTEGRATIONS_MANAGE)
        with self._uow:
            current = self._store.get()
            secret = command.secret or (current.secret if current else "")
            if not secret:
                raise DomainValidationError("The Sipuni integration key is required.")
            token = None
            if current is None or command.rotate_token:
                token = secrets.token_urlsafe(32)
            connection = SipuniConnection(
                account=command.account.strip(),
                secret=secret.strip(),
                company_number=command.company_number,
                fallback_owner_id=current.fallback_owner_id if current else principal.user_id,
                webhook_token_hash=token_hash(token) if token else current.webhook_token_hash,  # type: ignore[union-attr]
                extensions=command.extensions,
                country_code=command.country_code,
                call_language=command.call_language,
                record_channels=command.record_channels,
                analyze_calls=command.analyze_calls,
                enabled=command.enabled,
            )
            self._store.save(connection)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="telephony_connection.saved",
                    resource_type=CONNECTION,
                    resource_id="sipuni",
                    changed_fields=("token",) if token else (),
                )
            )
        return SavedConnection(connection=connection, webhook_token=token)

    def disconnect(self, principal: Principal) -> None:
        self._policy.require(principal, Permission.INTEGRATIONS_MANAGE)
        with self._uow:
            self._store.delete()
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="telephony_connection.deleted",
                    resource_type=CONNECTION,
                    resource_id="sipuni",
                )
            )

    # --- events (Sipuni, no principal) -------------------------------------------

    def active(self, token: str) -> SipuniConnection:
        """The connection this event address belongs to; NotFoundError otherwise, the same
        for an unknown company, a wrong token and a switched-off connection."""
        connection = self._store.get()
        if (
            connection is None
            or not connection.enabled
            or not hmac.compare_digest(connection.webhook_token_hash, token_hash(token))
        ):
            raise NotFoundError("Not found.")
        return connection

    def ingest(self, token: str, params: Mapping[str, str]) -> Call | None:
        """Log the call a hang-up event finishes. Repeated events change nothing."""
        connection = self.active(token)
        event = parse_event(params)
        external = finished_call(event, connection)
        if external is None:
            return None
        with self._uow:
            if self._calls.get_by_external_id(external.external_id) is not None:
                return None
            inbound = external.direction is CallDirection.INBOUND
            call, ended = Call.log(
                direction=external.direction,
                from_number=external.customer_phone if inbound else external.company_phone,
                to_number=external.company_phone if inbound else external.customer_phone,
                owner_user_id=external.owner_user_id,
                team_id=self._departments.department_of(external.owner_user_id),
                status=external.status,
                started_at=external.started_at,
                duration_seconds=external.duration_seconds,
                recording_url=external.recording_url,
                external_id=external.external_id,
                link=self._contacts.link_for(external.customer_phone),
            )
            self._calls.add(call)
            self._outbox.add(ended, aggregate_type=AGGREGATE, aggregate_id=str(call.id))
            self._audit.record(
                AuditEntry(
                    actor_user_id=None,
                    action="call.received",
                    resource_type=AGGREGATE,
                    resource_id=str(call.id),
                )
            )
        return call
