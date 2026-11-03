"""Wires the cabinet use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.organizations.composition import company_settings
from modules.team_cabinet.application.service import TeamCabinetService
from modules.team_cabinet.infrastructure.analytics import DjangoAnalyticsReader
from modules.team_cabinet.infrastructure.reader import DjangoCabinetReader, DjangoTeamRoster
from platform_core.application.context import require_tenant


def team_cabinet_service() -> TeamCabinetService:
    tenant = require_tenant()
    # Money in the cabinet is summed in the company currency only: amounts in other
    # currencies are never added to it (counts of payments include every currency).
    currency = str(company_settings(tenant.tenant_id)["currency"])
    return TeamCabinetService(
        roster=DjangoTeamRoster(),
        reader=DjangoCabinetReader(tenant.db_alias, currency),
        analytics=DjangoAnalyticsReader(tenant.db_alias, currency),
    )
