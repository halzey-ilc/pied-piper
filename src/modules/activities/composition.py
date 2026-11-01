"""Wires the activity use cases to adapters bound to the active tenant database."""

from __future__ import annotations

from uuid import UUID

from modules.activities.application.service import ActivityService
from modules.activities.infrastructure.repository import DjangoActivityRepository
from modules.activities.infrastructure.subject_checker import DjangoSubjectChecker
from modules.audit.domain.entry import AuditEntry
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.organizations.infrastructure.memberships import DjangoMemberDirectory
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


def activity_service() -> ActivityService:
    tenant = require_tenant()
    alias = tenant.db_alias
    return ActivityService(
        repository=DjangoActivityRepository(alias),
        subjects=DjangoSubjectChecker(alias),
        members=DjangoMemberDirectory(tenant.tenant_id),
        uow=DjangoUnitOfWork(alias),
        outbox=DjangoEventOutbox(alias),
        audit=DjangoAuditLog(alias),
    )


def hand_over_open_tasks(from_user: UUID, to_user: UUID, to_team: UUID | None, actor: UUID) -> int:
    """Staff offboarding (called by organizations after the director's decision): the open
    tasks of a deactivated member go to ``to_user``. Done and cancelled tasks keep their
    assignee as history. Audited in the company's database."""
    alias = require_tenant().db_alias
    with DjangoUnitOfWork(alias):
        moved = DjangoActivityRepository(alias).hand_over_open_tasks(from_user, to_user, to_team)
        if moved:
            DjangoAuditLog(alias).record(
                AuditEntry(
                    actor_user_id=actor,
                    action="activity.tasks_handed_over",
                    resource_type="user",
                    resource_id=str(from_user),
                    changed_fields=("assignee",),
                )
            )
    return moved
