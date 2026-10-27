"""ProvisionTenant use case with in-memory fakes (no database is created)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from modules.tenant_registry.application.provisioning import (
    ProvisioningDisabledError,
    ProvisionTenant,
    ProvisionTenantCommand,
)
from modules.tenant_registry.domain.placement import ClusterCapacity, ProvisioningStatus
from platform_core.domain.tenancy import TenantDatabaseLocator, TenantStatus
from shared.domain.errors import ConflictError


class FakeRegistry:
    def __init__(self) -> None:
        self.rows: dict[UUID, TenantDatabaseLocator] = {}
        self.jobs: dict[UUID, ProvisioningStatus] = {}

    def get(self, organization_id: UUID) -> TenantDatabaseLocator | None:
        return self.rows.get(organization_id)

    def cluster_capacity(self) -> Mapping[str, ClusterCapacity]:
        return {"primary": ClusterCapacity("primary", True, 10, len(self.rows))}

    def register(
        self, organization_id: UUID, slug: str, cluster: str, database_name: str
    ) -> TenantDatabaseLocator:
        locator = TenantDatabaseLocator(
            organization_id, slug, cluster, database_name, TenantStatus.PROVISIONING
        )
        self.rows.setdefault(organization_id, locator)
        return self.rows[organization_id]

    def mark_active(self, organization_id: UUID, schema_version: str) -> None:
        self.rows[organization_id] = replace(
            self.rows[organization_id], status=TenantStatus.ACTIVE, schema_version=schema_version
        )

    def mark_failed(self, organization_id: UUID) -> None:
        if organization_id in self.rows:
            self.rows[organization_id] = replace(
                self.rows[organization_id], status=TenantStatus.FAILED
            )

    def start_job(self, organization_id: UUID, requested_by: UUID | None) -> UUID:
        if any(
            s not in (ProvisioningStatus.SUCCEEDED, ProvisioningStatus.FAILED)
            for s in self.jobs.values()
        ):
            raise ConflictError("busy")
        job_id = uuid4()
        self.jobs[job_id] = ProvisioningStatus.PENDING
        return job_id

    def update_job(
        self, job_id: UUID, status: ProvisioningStatus, error: str | None = None
    ) -> None:
        self.jobs[job_id] = status


class FakeProvisioner:
    def __init__(self, fail: bool = False) -> None:
        self.created: set[str] = set()
        self.fail = fail

    def create_database(self, cluster: str, database_name: str) -> bool:
        if self.fail:
            raise OSError("cluster unreachable")
        if database_name in self.created:
            return False
        self.created.add(database_name)
        return True


class FakeMigrator:
    def migrate(self, locator: TenantDatabaseLocator) -> str:
        return "v1"


def _use_case(
    registry: FakeRegistry, provisioner: FakeProvisioner, enabled: bool = True
) -> ProvisionTenant:
    return ProvisionTenant(
        registry, provisioner, FakeMigrator(), database_prefix="crm_tenant_", enabled=enabled
    )


def test_provisioning_happy_path_and_idempotency() -> None:
    registry, provisioner = FakeRegistry(), FakeProvisioner()
    org = uuid4()
    result = _use_case(registry, provisioner).execute(ProvisionTenantCommand(org, "acme-co"))
    assert result.database_name == "crm_tenant_acme_co"
    assert result.created_database and not result.already_active
    assert registry.rows[org].status is TenantStatus.ACTIVE
    assert list(registry.jobs.values()) == [ProvisioningStatus.SUCCEEDED]

    again = _use_case(registry, provisioner).execute(ProvisionTenantCommand(org, "acme-co"))
    assert again.already_active and not again.created_database
    assert len(registry.jobs) == 1  # no new job for an active tenant


def test_failed_provisioning_marks_job_and_tenant_failed() -> None:
    registry = FakeRegistry()
    org = uuid4()
    with pytest.raises(OSError, match="unreachable"):
        _use_case(registry, FakeProvisioner(fail=True)).execute(ProvisionTenantCommand(org, "acme"))
    assert registry.rows[org].status is TenantStatus.FAILED
    assert list(registry.jobs.values()) == [ProvisioningStatus.FAILED]

    # Re-running after the cause is fixed resumes with the same registry row.
    result = _use_case(registry, FakeProvisioner()).execute(ProvisionTenantCommand(org, "acme"))
    assert result.database_name == "crm_tenant_acme"
    assert registry.rows[org].status is TenantStatus.ACTIVE


def test_provisioning_disabled_by_default_configuration() -> None:
    with pytest.raises(ProvisioningDisabledError):
        _use_case(FakeRegistry(), FakeProvisioner(), enabled=False).execute(
            ProvisionTenantCommand(uuid4(), "acme")
        )
