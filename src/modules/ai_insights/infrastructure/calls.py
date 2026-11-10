"""Reads calls through the telephony module's public composition API.

Visibility reuses telephony's own RBAC check (``CallService.get``), so this
module cannot see a call the user could not open in the CRM.
"""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.application.ports import CallSnapshot
from modules.ai_insights.domain.call_analysis import transcript_text
from modules.ai_insights.infrastructure.call_analysis import DjangoAnalysisRepository
from modules.ai_insights.infrastructure.names import customer_names, deal_customer_names
from modules.telephony.composition import call_repository, call_service
from modules.telephony.domain.call import SubjectType
from platform_core.application.context import require_tenant


def _transcript(call_id: UUID) -> str | None:
    analysis = DjangoAnalysisRepository(require_tenant().db_alias).latest_for(call_id)
    if analysis is None or not analysis.segments:
        return None
    return transcript_text(analysis.segments, analysis.language)


class TelephonyCallReader:
    def get_visible(self, principal: Principal, call_id: UUID) -> CallSnapshot:
        call = call_service().get(principal, call_id)  # NotFoundError if not visible
        return CallSnapshot(
            id=call.id,
            owner_user_id=call.owner_user_id,
            team_id=call.team_id,
            text=_transcript(call.id) or call.notes or "",
        )

    def text_for(self, call_id: UUID) -> str | None:
        """The transcript of the recording when there is one, else the manager's notes."""
        call = call_repository().get(call_id)
        return None if call is None else _transcript(call_id) or call.notes

    def names_for(self, call_id: UUID) -> tuple[str, ...]:
        call = call_repository().get(call_id)
        if call is None or call.link is None:
            return ()
        if call.link.type is SubjectType.CUSTOMER:
            return customer_names(call.link.id)
        return deal_customer_names(call.link.id)
