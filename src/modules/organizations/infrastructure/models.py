from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from modules.access_control.domain.rbac import Role


class Organization(models.Model):
    """A customer company. Each active organization owns exactly one tenant database."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "organizations_organization"

    def __str__(self) -> str:
        return self.slug


class Membership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=32, choices=[(r.value, r.name.title()) for r in Role])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "organizations_membership"
        constraints = [
            models.UniqueConstraint(fields=["user", "organization"], name="membership_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}@{self.organization_id}:{self.role}"
