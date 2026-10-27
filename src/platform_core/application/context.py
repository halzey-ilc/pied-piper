"""Per-request / per-task execution context (tenant, request id, correlation id).

Implemented with contextvars so it is isolated between threads and async tasks.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID

from platform_core.domain.tenancy import TenantContextMissingError

_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: UUID
    slug: str
    db_alias: str


_tenant: ContextVar[TenantContext | None] = ContextVar("tenant", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def current_tenant() -> TenantContext | None:
    return _tenant.get()


def require_tenant() -> TenantContext:
    tenant = _tenant.get()
    if tenant is None:
        raise TenantContextMissingError("No active tenant context.")
    return tenant


def activate_tenant(tenant: TenantContext) -> Token[TenantContext | None]:
    return _tenant.set(tenant)


def deactivate_tenant(token: Token[TenantContext | None]) -> None:
    _tenant.reset(token)


@contextmanager
def tenant_scope(tenant: TenantContext) -> Iterator[TenantContext]:
    token = activate_tenant(tenant)
    try:
        yield tenant
    finally:
        deactivate_tenant(token)


def new_id() -> str:
    return uuid.uuid4().hex


def sanitize_id(candidate: str | None) -> str | None:
    """Accept externally supplied ids only if they are short and log-safe."""
    if candidate and _ID_RE.match(candidate):
        return candidate
    return None


def current_request_id() -> str | None:
    return _request_id.get()


def current_correlation_id() -> str | None:
    return _correlation_id.get()


@contextmanager
def correlation_scope(
    *, request_id: str | None = None, correlation_id: str | None = None
) -> Iterator[tuple[str, str]]:
    rid = request_id or new_id()
    cid = correlation_id or rid
    rid_token = _request_id.set(rid)
    cid_token = _correlation_id.set(cid)
    try:
        yield rid, cid
    finally:
        _request_id.reset(rid_token)
        _correlation_id.reset(cid_token)
