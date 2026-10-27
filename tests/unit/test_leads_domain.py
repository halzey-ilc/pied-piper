from __future__ import annotations

from uuid import uuid4

import pytest

from modules.leads.domain.lead import (
    Lead,
    LeadCaptured,
    LeadConverted,
    LeadQualified,
    LeadSource,
    LeadStateError,
    LeadStatus,
)
from shared.domain.errors import DomainValidationError


def _new_lead() -> Lead:
    lead, _ = Lead.capture(name="Jane Buyer", owner_user_id=uuid4(), email="jane@example.test")
    return lead


def test_capture_normalises_and_requires_contact() -> None:
    owner = uuid4()
    lead, event = Lead.capture(
        name="  Jane   Buyer ",
        owner_user_id=owner,
        source=LeadSource.WEBSITE,
        phone="+7 701 000 11 22",
    )
    assert lead.name == "Jane Buyer"
    assert str(lead.phone) == "+77010001122"
    assert lead.status is LeadStatus.NEW
    assert isinstance(event, LeadCaptured)
    assert event.payload()["source"] == "website"


def test_capture_without_any_contact_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        Lead.capture(name="No Contact", owner_user_id=uuid4())


def test_full_lifecycle_new_qualify_convert() -> None:
    lead = _new_lead()
    qualified = lead.qualify()
    assert isinstance(qualified, LeadQualified)
    assert lead.status.value == "qualified"
    customer_id = uuid4()
    event = lead.convert(customer_id)
    assert isinstance(event, LeadConverted)
    assert lead.status.value == "converted"
    assert lead.converted_customer_id == customer_id
    assert event.payload() == {"lead_id": str(lead.id), "customer_id": str(customer_id)}


def test_cannot_qualify_an_already_qualified_lead() -> None:
    lead = _new_lead()
    lead.qualify()
    with pytest.raises(LeadStateError):
        lead.qualify()


def test_cannot_convert_unless_qualified() -> None:
    lead = _new_lead()
    with pytest.raises(LeadStateError):
        lead.convert(uuid4())


def test_disqualify_then_requalify() -> None:
    lead = _new_lead()
    lead.disqualify("budget too low")
    # Compare .value, not `is`: mypy cannot see that the mutating calls change status.
    assert lead.status.value == "unqualified"
    reason = lead.disqualify_reason
    assert reason == "budget too low"
    lead.qualify()
    assert lead.status.value == "qualified"
    assert lead.disqualify_reason is None


def test_disqualify_requires_reason() -> None:
    with pytest.raises(DomainValidationError):
        _new_lead().disqualify("   ")


def test_converted_lead_is_terminal() -> None:
    lead = _new_lead()
    lead.qualify()
    lead.convert(uuid4())
    with pytest.raises(LeadStateError):
        lead.qualify()
    with pytest.raises(LeadStateError):
        lead.disqualify("x")
    with pytest.raises(LeadStateError):
        lead.update_details(name="New")


def test_update_details_bumps_version_only_on_change() -> None:
    lead = _new_lead()
    assert lead.version == 1
    assert lead.update_details(name="Jane B.") is True
    assert lead.version == 2
    assert lead.update_details(name="Jane B.") is False
    assert lead.version == 2


def test_update_cannot_remove_last_contact() -> None:
    lead, _ = Lead.capture(name="Only Email", owner_user_id=uuid4(), email="a@example.test")
    with pytest.raises(DomainValidationError):
        lead.update_details(email="")
