from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q

from modules.organizations.infrastructure.models import Organization
from modules.tenant_registry.domain.placement import (
    ACTIVE_PROVISIONING_STATUSES,
    ProvisioningStatus,
)
from platform_core.domain.tenancy import TenantStatus


class TenantDatabase(models.Model):
    """Registry entry: the single database that holds one organization's business data.

    Stores only the cluster key and database name. Credentials live in server-side
    configuration (settings.DATABASE_CLUSTERS), never in this table or in requests.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        Organization, on_delete=models.PROTECT, related_name="tenant_database"
    )
    slug = models.SlugField(max_length=40, unique=True)
    cluster = models.CharField(max_length=64)
    database_name = models.CharField(max_length=63, unique=True)
    status = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in TenantStatus],
        default=TenantStatus.PROVISIONING.value,
    )
    schema_version = models.CharField(max_length=32, blank=True, default="")
    last_migrated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenant_registry_database"
        indexes = [models.Index(fields=["cluster", "status"], name="tenant_db_cluster_status_idx")]

    def __str__(self) -> str:
        return f"{self.slug} -> {self.cluster}/{self.database_name}"


class ProvisioningJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="provisioning_jobs"
    )
    status = models.CharField(
        max_length=24,
        choices=[(s.value, s.name.title()) for s in ProvisioningStatus],
        default=ProvisioningStatus.PENDING.value,
    )
    requested_by = models.UUIDField(null=True, blank=True)
    error = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tenant_registry_provisioning_job"
        constraints = [
            # At most one in-flight provisioning job per organization.
            models.UniqueConstraint(
                fields=["organization"],
                condition=Q(status__in=[s.value for s in ACTIVE_PROVISIONING_STATUSES]),
                name="provisioning_one_active_job_per_org",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.status}"
