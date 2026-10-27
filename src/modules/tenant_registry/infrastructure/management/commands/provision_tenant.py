"""Provision a tenant database for an organization (idempotent).

Requires TENANT_PROVISIONING_ENABLED=true. Example (local):

    python manage.py provision_tenant demo --name "Demo Company" --create-organization \
        --owner-username admin
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser

from modules.access_control.domain.rbac import Role
from modules.organizations.infrastructure.models import Membership, Organization
from modules.tenant_registry.application.provisioning import ProvisionTenantCommand
from modules.tenant_registry.composition import build_provision_tenant
from platform_core.domain.tenancy import validate_tenant_slug
from shared.domain.errors import DomainError


class Command(BaseCommand):
    help = "Create, migrate and activate the database of one tenant."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("slug")
        parser.add_argument("--name", help="Organization name (with --create-organization)")
        parser.add_argument("--create-organization", action="store_true")
        parser.add_argument("--owner-username", help="Existing user to grant the owner role")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            slug = validate_tenant_slug(options["slug"])
        except DomainError as exc:
            raise CommandError(str(exc)) from None

        org = Organization.objects.using("default").filter(slug=slug).first()
        if org is None:
            if not options["create_organization"]:
                raise CommandError(
                    f"Organization {slug!r} does not exist (use --create-organization)."
                )
            org = Organization.objects.using("default").create(
                slug=slug, name=options["name"] or slug
            )
            self.stdout.write(f"Created organization {slug}")

        try:
            result = build_provision_tenant().execute(
                ProvisionTenantCommand(organization_id=org.id, slug=slug)
            )
        except DomainError as exc:
            raise CommandError(str(exc)) from None

        state = "already active" if result.already_active else "provisioned"
        self.stdout.write(
            self.style.SUCCESS(
                f"Tenant {slug} {state}: cluster={result.cluster} database={result.database_name} "
                f"schema_version={result.schema_version} created_database={result.created_database}"
            )
        )

        username = options.get("owner_username")
        if username:
            user = get_user_model().objects.using("default").filter(username=username).first()
            if user is None:
                raise CommandError(f"User {username!r} does not exist.")
            Membership.objects.using("default").update_or_create(
                user=user, organization=org, defaults={"role": Role.OWNER.value, "is_active": True}
            )
            self.stdout.write(f"Granted owner role in {slug} to {username}")
