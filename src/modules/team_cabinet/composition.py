"""Wires the cabinet use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from modules.team_cabinet.application.service import TeamCabinetService
from modules.team_cabinet.infrastructure.reader import DjangoCabinetReader, DjangoTeamRoster
from platform_core.application.context import require_tenant


def team_cabinet_service() -> TeamCabinetService:
    alias = require_tenant().db_alias
    return TeamCabinetService(roster=DjangoTeamRoster(), reader=DjangoCabinetReader(alias))
