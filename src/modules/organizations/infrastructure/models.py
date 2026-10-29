from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from modules.access_control.domain.rbac import Role


class Organization(models.Model):
    """A customer company. Each active organization owns exactly one tenant database."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    # Set when the company is created; reports by day/week/month use this timezone.
    country = models.CharField(max_length=2, default="KG")  # ISO 3166-1 alpha-2
    currency = models.CharField(max_length=3, default="KGS")  # ISO 4217, default for new deals
    timezone = models.CharField(max_length=64, default="Asia/Bishkek")  # IANA name
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "organizations_organization"

    def __str__(self) -> str:
        return self.slug


class Department(models.Model):
    """A sales department (team) inside a company; its id is the team_id of records."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="departments"
    )
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "organizations_department"
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="department_name_unique"),
        ]

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=32, choices=[(r.value, r.name.title()) for r in Role])
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, null=True, blank=True, related_name="members"
    )
    is_active = models.BooleanField(default=True)
    created_by = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "organizations_membership"
        constraints = [
            models.UniqueConstraint(fields=["user", "organization"], name="membership_unique"),
            # One person, one company: a second company needs a separate account.
            models.UniqueConstraint(
                fields=["user"], condition=Q(is_active=True), name="membership_one_active_per_user"
            ),
        ]
        indexes = [models.Index(fields=["organization", "department"], name="membership_dept_idx")]

    def __str__(self) -> str:
        return f"{self.user_id}@{self.organization_id}:{self.role}"
