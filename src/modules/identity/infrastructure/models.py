from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Global user account (control plane). Tenant access is granted via memberships."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)

    class Meta:
        db_table = "identity_user"
