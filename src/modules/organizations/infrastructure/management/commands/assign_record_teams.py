"""Attribute existing tenant records to their owner's department (team_id).

Records created before departments existed have ``team_id`` NULL, so a head of
sales would not see them. This command fills ``team_id`` from the owner's
current department, only where it is empty; it never overwrites a value and
never deletes anything. Safe to run repeatedly.

    python manage.py assign_record_teams [--tenant SLUG] [--dry-run]
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand

from modules.organizations.infrastructure.models import Membership, Organization
from platform_core.infrastructure.tenancy.services import system_tenant_scope


def _tenant_models() -> list[Any]:
    out = []
    for model in apps.get_models():
        names = {f.name for f in model._meta.get_fields()}
        if {"owner_user_id", "team_id"} <= names and model._meta.app_label not in {
            "organizations",
            "identity",
        }:
            out.append(model)
    return out


class Command(BaseCommand):
    help = "Fill empty team_id of tenant records from the owner's department."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--tenant", help="Only this tenant slug")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        orgs = Organization.objects.using("default").filter(is_active=True)
        if options["tenant"]:
            orgs = orgs.filter(slug=options["tenant"])
        for org in orgs:
            departments = dict(
                Membership.objects.using("default")
                .filter(organization=org, department__isnull=False)
                .values_list("user_id", "department_id")
            )
            if not departments:
                self.stdout.write(f"{org.slug}: no department assignments, nothing to do")
                continue
            with system_tenant_scope(org.slug) as tenant:
                for model in _tenant_models():
                    total = 0
                    for user_id, department_id in departments.items():
                        qs = model.objects.using(tenant.db_alias).filter(
                            owner_user_id=user_id, team_id__isnull=True
                        )
                        total += (
                            qs.count() if options["dry_run"] else qs.update(team_id=department_id)
                        )
                    if total:
                        verb = "would update" if options["dry_run"] else "updated"
                        self.stdout.write(f"{org.slug}: {model._meta.db_table}: {verb} {total}")
