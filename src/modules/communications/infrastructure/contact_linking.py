"""ContactLinker adapter: ties a WhatsApp number to a lead or customer.

A WhatsApp contact id is the phone number in digits, so it matches CRM records
directly. Other channels carry no phone number and stay unlinked.
"""

from __future__ import annotations

import logging
from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.communications.application.ports import ContactLink
from modules.communications.domain.conversation import Channel
from modules.leads.application.service import LeadService
from modules.leads.domain.lead import LeadSource
from shared.domain.errors import DomainValidationError

logger = logging.getLogger(__name__)


class LeadContactLinker:
    def __init__(self, leads: LeadService, owner_user_id: UUID | None) -> None:
        self._leads = leads
        self._owner_user_id = owner_user_id

    def hand_over(
        self, principal: Principal, lead_id: UUID, owner_user_id: UUID, team_id: UUID | None
    ) -> None:
        self._leads.hand_over(principal, lead_id, owner_user_id, team_id)

    def link(
        self, *, channel: Channel, external_contact_id: str, contact_name: str | None
    ) -> ContactLink:
        if channel is not Channel.WHATSAPP:
            return ContactLink()
        try:
            match = self._leads.match_or_capture_contact(
                phone=f"+{external_contact_id}",
                name=contact_name,
                source=LeadSource.WHATSAPP,
                owner_user_id=self._owner_user_id,
            )
        except DomainValidationError:
            # The message matters more than the link: keep it, leave it unlinked.
            logger.warning("contact_link_invalid_phone", extra={"channel": channel.value})
            return ContactLink()
        if match.lead_id is None and match.customer_id is None:
            logger.warning("contact_link_no_lead_owner", extra={"channel": channel.value})
        return ContactLink(lead_id=match.lead_id, customer_id=match.customer_id)
