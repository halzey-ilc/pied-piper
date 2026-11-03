"""The ``NextSteps`` port for other contexts (meetings, deals).

Runs on the active tenant database, so the task joins the caller's open transaction. The
caller has already loaded the deal/customer with the principal's rights, so the subject is
not looked up again (that lookup would wire activities back to deals and meetings).
"""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.activities.application.next_step import NextStep
from modules.activities.application.service import ActivityService, LogTask
from modules.activities.domain.activity import SubjectRef, SubjectType
from modules.activities.infrastructure.repository import DjangoActivityRepository
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.organizations.infrastructure.memberships import DjangoMemberDirectory
from platform_core.application.context import require_tenant
from platform_core.infrastructure.messaging.stores import DjangoEventOutbox
from shared.infrastructure.unit_of_work import DjangoUnitOfWork


class _CheckedByCaller:
    def visible_to(self, principal: Principal, subject_type: SubjectType, subject_id: UUID) -> bool:
        return True


class ActivityNextSteps:
    def has_open_dated_task(self, subject_type: str, subject_id: UUID) -> bool:
        subject = SubjectRef(SubjectType(subject_type), subject_id)
        return DjangoActivityRepository(require_tenant().db_alias).has_open_dated_task(subject)

    def create(
        self,
        principal: Principal,
        subject_type: str,
        subject_id: UUID,
        step: NextStep,
        *,
        default_assignee: UUID,
    ) -> UUID:
        tenant = require_tenant()
        alias = tenant.db_alias
        service = ActivityService(
            repository=DjangoActivityRepository(alias),
            subjects=_CheckedByCaller(),
            members=DjangoMemberDirectory(tenant.tenant_id),
            uow=DjangoUnitOfWork(alias),
            outbox=DjangoEventOutbox(alias),
            audit=DjangoAuditLog(alias),
        )
        task = service.log_task(
            principal,
            LogTask(
                subject=SubjectRef(SubjectType(subject_type), subject_id),
                title=step.title,
                due_date=step.due_date,
                assignee_user_id=step.assignee_user_id or default_assignee,
            ),
        )
        return task.id
