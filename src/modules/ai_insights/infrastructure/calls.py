"""Reads calls through the telephony module's public composition API.

Visibility reuses telephony's own RBAC check (``CallService.get``), so this
module cannot see a call the user could not open in the CRM.
"""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.application.ports import CallSnapshot
from modules.telephony.composition import call_repository, call_service


class TelephonyCallReader:
    def get_visible(self, principal: Principal, call_id: UUID) -> CallSnapshot:
        call = call_service().get(principal, call_id)  # NotFoundError if not visible
        return CallSnapshot(
            id=call.id,
            owner_user_id=call.owner_user_id,
            team_id=call.team_id,
            text=call.notes or "",
        )

    def text_for(self, call_id: UUID) -> str | None:
        call = call_repository().get(call_id)
        return None if call is None else call.notes
