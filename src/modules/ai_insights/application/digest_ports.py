from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.domain.digest import DigestTrigger, OperationsDigest


@dataclass(frozen=True, slots=True)
class FindingsSnapshot:
    """What the operations rules found for a principal, as digest input parts."""

    thresholds: dict[str, int]
    counts: dict[str, int]
    findings: list[dict[str, Any]]  # {"ref", "kind", "age_days"}, oldest first


class FindingsSource(Protocol):
    def snapshot_for(self, principal: Principal) -> FindingsSnapshot: ...


@dataclass(frozen=True, slots=True)
class Member:
    user_id: UUID
    role: str


class MemberDirectory(Protocol):
    def active_members(self, tenant_id: UUID) -> list[Member]: ...


class DigestRepository(Protocol):
    def add(self, digest: OperationsDigest) -> None: ...

    def get(self, digest_id: UUID) -> OperationsDigest | None: ...

    def latest_for_user(self, user_id: UUID) -> OperationsDigest | None: ...

    def find_reusable(self, user_id: UUID, payload_hash: str) -> OperationsDigest | None:
        """An unfinished or succeeded digest of the same snapshot, if any."""
        ...

    def exists_for_day(self, user_id: UUID, trigger: DigestTrigger, day: date) -> bool: ...

    def save(self, digest: OperationsDigest, expected_version: int) -> None: ...


class DigestScheduler(Protocol):
    def schedule_submit(self, *, tenant_slug: str, digest_id: UUID) -> None: ...

    def schedule_poll(self, *, tenant_slug: str, digest_id: UUID, delay_seconds: float) -> None: ...
