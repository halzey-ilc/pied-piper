from __future__ import annotations

from modules.audit.application.journal import AuditJournalService
from modules.audit.infrastructure.journal import DjangoAuditJournal, department_members
from platform_core.application.context import require_tenant


def audit_journal_service() -> AuditJournalService:
    # The company's audit trail lives in its own database.
    return AuditJournalService(
        reader=DjangoAuditJournal(require_tenant().db_alias),
        department_members=department_members,
    )
