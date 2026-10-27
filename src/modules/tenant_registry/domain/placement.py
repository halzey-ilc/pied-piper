"""Cluster placement policy for new tenant databases."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from shared.domain.errors import ConflictError


class ProvisioningStatus(StrEnum):
    PENDING = "pending"
    CREATING_DATABASE = "creating_database"
    MIGRATING = "migrating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


ACTIVE_PROVISIONING_STATUSES = (
    ProvisioningStatus.PENDING,
    ProvisioningStatus.CREATING_DATABASE,
    ProvisioningStatus.MIGRATING,
)


@dataclass(frozen=True, slots=True)
class ClusterCapacity:
    key: str
    accepts_new_tenants: bool
    max_tenants: int
    current_tenants: int

    @property
    def free_slots(self) -> int:
        return self.max_tenants - self.current_tenants


class NoClusterCapacityError(ConflictError):
    code = "no_cluster_capacity"


def choose_cluster(clusters: Mapping[str, ClusterCapacity]) -> str:
    """Pick the open cluster with the most free slots (ties broken by key for determinism)."""
    candidates = [c for c in clusters.values() if c.accepts_new_tenants and c.free_slots > 0]
    if not candidates:
        raise NoClusterCapacityError("No database cluster can accept a new tenant.")
    return sorted(candidates, key=lambda c: (-c.free_slots, c.key))[0].key
