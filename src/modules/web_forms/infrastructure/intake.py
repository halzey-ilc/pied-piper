"""Creates a website lead through the existing use cases of the active tenant."""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.activities.application.service import LogNote
from modules.activities.composition import activity_service
from modules.activities.domain.activity import SubjectRef, SubjectType
from modules.audit.domain.entry import AuditEntry
from modules.audit.infrastructure.audit_log import DjangoAuditLog
from modules.leads.application.service import CaptureLead
from modules.leads.composition import lead_service
from modules.leads.domain.lead import LeadSource
from modules.organizations.infrastructure.models import Organization
from modules.web_forms.domain.key import IntakeKey, WebsiteSubmission, international_phone
from platform_core.application.context import require_tenant
from shared.infrastructure.unit_of_work import DjangoUnitOfWork

NOTE_TITLE = "Заявка с сайта"  # noqa: RUF001


class ServiceLeadIntake:
    def capture(self, principal: Principal, key: IntakeKey, submission: WebsiteSubmission) -> UUID:
        alias = require_tenant().db_alias
        country = (
            Organization.objects.using("default")
            .filter(id=key.tenant_id)
            .values_list("country", flat=True)
            .first()
        )
        with DjangoUnitOfWork(alias):
            # Same path as a lead captured by the head of sales: routing, events, audit.
            lead = lead_service().capture(
                principal,
                CaptureLead(
                    name=submission.name,
                    source=LeadSource.WEBSITE,
                    email=submission.email or None,
                    phone=international_phone(submission.phone, country or "") or None,
                ),
            )
            note = submission.note()
            if note is not None:
                activity_service().log_note(
                    principal,
                    LogNote(
                        subject=SubjectRef(SubjectType.LEAD, lead.id), body=note, title=NOTE_TITLE
                    ),
                )
            DjangoAuditLog(alias).record(
                AuditEntry(
                    actor_user_id=None,
                    action="web_form.lead_received",
                    resource_type="lead",
                    resource_id=str(lead.id),
                    details={"key_id": str(key.id), "department_id": str(key.department_id)},
                )
            )
        return lead.id
