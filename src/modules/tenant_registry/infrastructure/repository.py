from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from modules.tenant_registry.domain.placement import (
    ClusterCapacity,
    ProvisioningStatus,
)
from modules.tenant_registry.infrastructure.directory import to_locator
from modules.tenant_registry.infrastructure.models import ProvisioningJob, TenantDatabase
from platform_core.domain.tenancy import TenantDatabaseLocator, TenantStatus
from shared.domain.errors import ConflictError

_TERMINAL = (ProvisioningStatus.SUCCEEDED, ProvisioningStatus.FAILED)


class DjangoTenantRegistryRepository:
    def __init__(self, clusters: Mapping[str, Mapping[str, Any]]) -> None:
        self._clusters = clusters

    def get(self, organization_id: UUID) -> TenantDatabaseLocator | None:
        row = (
            TenantDatabase.objects.using("default").filter(organization_id=organization_id).first()
        )
        return None if row is None else to_locator(row)

    def cluster_capacity(self) -> Mapping[str, ClusterCapacity]:
        counts = dict(
            TenantDatabase.objects.using("default")
            .values_list("cluster")
            .annotate(n=Count("id"))
            .values_list("cluster", "n")
        )
        return {
            key: ClusterCapacity(
                key=key,
                accepts_new_tenants=bool(cfg.get("ACCEPTS_NEW_TENANTS", False)),
                max_tenants=int(cfg.get("MAX_TENANTS", 0)),
                current_tenants=int(counts.get(key, 0)),
            )
            for key, cfg in self._clusters.items()
        }

    def register(
        self, organization_id: UUID, slug: str, cluster: str, database_name: str
    ) -> TenantDatabaseLocator:
        row, _ = TenantDatabase.objects.using("default").get_or_create(
            organization_id=organization_id,
            defaults={
                "slug": slug,
                "cluster": cluster,
                "database_name": database_name,
                "status": TenantStatus.PROVISIONING.value,
            },
        )
        return to_locator(row)

    def mark_active(self, organization_id: UUID, schema_version: str) -> None:
        TenantDatabase.objects.using("default").filter(organization_id=organization_id).update(
            status=TenantStatus.ACTIVE.value,
            schema_version=schema_version,
            last_migrated_at=timezone.now(),
            updated_at=timezone.now(),
        )

    def mark_failed(self, organization_id: UUID) -> None:
        TenantDatabase.objects.using("default").filter(organization_id=organization_id).exclude(
            status=TenantStatus.ACTIVE.value
        ).update(status=TenantStatus.FAILED.value, updated_at=timezone.now())

    def start_job(self, organization_id: UUID, requested_by: UUID | None) -> UUID:
        try:
            with transaction.atomic(using="default"):
                job = ProvisioningJob.objects.using("default").create(
                    organization_id=organization_id, requested_by=requested_by
                )
        except IntegrityError:
            raise ConflictError("A provisioning job is already running for this tenant.") from None
        return job.id

    def update_job(
        self, job_id: UUID, status: ProvisioningStatus, error: str | None = None
    ) -> None:
        fields: dict[str, Any] = {"status": status.value, "updated_at": timezone.now()}
        if error is not None:
            fields["error"] = error[:200]
        if status in _TERMINAL:
            fields["finished_at"] = timezone.now()
        ProvisioningJob.objects.using("default").filter(id=job_id).update(**fields)
