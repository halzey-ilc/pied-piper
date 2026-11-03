"""How much of its plan a company uses: counted where each record lives.

Accounts are global (control plane); customers and deals are the company's own data
(its tenant database, ``db_alias``).
"""

from __future__ import annotations

from uuid import UUID

from modules.customers.infrastructure.models import CustomerRecord
from modules.organizations.infrastructure.models import Membership
from modules.sales_pipeline.infrastructure.models import DealRecord
from modules.tenant_registry.infrastructure.directory import to_locator
from modules.tenant_registry.infrastructure.models import TenantDatabase
from platform_core.infrastructure.tenancy.services import connection_registry


def count_usage(tenant_id: UUID, db_alias: str, metric: str | None = None) -> dict[str, int]:
    """Current counts per limit metric; with ``metric`` only that one is counted."""
    counters = {
        "users": lambda: (
            Membership.objects.using("default")
            .filter(organization_id=tenant_id, is_active=True, user__is_active=True)
            .count()
        ),
        # Archived customers keep their history but no longer count against the plan.
        "customers": lambda: (
            CustomerRecord.objects.using(db_alias).filter(archived_at__isnull=True).count()
        ),
        "deals": lambda: DealRecord.objects.using(db_alias).count(),
    }
    wanted = [metric] if metric is not None else list(counters)
    return {name: counters[name]() for name in wanted}


def company_usage(tenant_id: UUID) -> dict[str, int] | None:
    """Usage of any company (platform administrator); None if its database is not ready."""
    row = TenantDatabase.objects.using("default").filter(organization_id=tenant_id).first()
    if row is None:
        return None
    locator = to_locator(row)
    if not locator.is_routable:
        return None
    return count_usage(tenant_id, connection_registry().ensure_alias(locator))
