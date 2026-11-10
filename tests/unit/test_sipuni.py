"""Sipuni events, signatures and phone numbers (doc.sipuni.com, section API)."""

from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from modules.telephony.domain.call import CallDirection, CallStatus
from modules.telephony.domain.sipuni import (
    SipuniConnection,
    finished_call,
    normalize_phone,
    parse_event,
    sign,
)
from shared.domain.errors import DomainValidationError

MANAGER, OTHER, DIRECTOR = uuid4(), uuid4(), uuid4()


def _connection(**kw: object) -> SipuniConnection:
    values: dict[str, object] = {
        "account": "012345",
        "secret": "key",
        "company_number": "0312123456",
        "fallback_owner_id": DIRECTOR,
        "webhook_token_hash": "h",
        "extensions": {"101": MANAGER, "102": OTHER},
    }
    values.update(kw)
    return SipuniConnection(**values)  # type: ignore[arg-type]


def _hangup(**kw: str) -> dict[str, str]:
    event = {
        "event": "2",
        "call_id": "1700000000.42",
        "src_num": "996555123456",
        "src_type": "1",
        "dst_num": "012345101",
        "dst_type": "2",
        "timestamp": "1700000300",
        "status": "ANSWER",
        "call_start_timestamp": "1700000000",
        "call_answer_timestamp": "1700000010",
        "call_record_link": "https://sipuni.com/api/crm/record?id=1&hash=x",
    }
    event.update(kw)
    return event


def test_signature_is_md5_of_values_and_key_joined_with_plus() -> None:
    expected = hashlib.md5(b"0+996555123456+0+101+012345+key").hexdigest()  # noqa: S324
    assert sign(["0", "996555123456", "0", "101", "012345"], "key") == expected


@pytest.mark.parametrize(
    ("raw", "phone"),
    [
        ("996555123456", "+996555123456"),
        ("+7 (903) 123-45-67", "+79031234567"),
        ("0555123456", "+996555123456"),
        ("555123456", "+996555123456"),
        ("101", None),
    ],
)
def test_phone_numbers_become_e164(raw: str, phone: str | None) -> None:
    assert normalize_phone(raw, "996") == phone


def test_inbound_answered_call_is_owned_by_the_extension_and_keeps_its_recording() -> None:
    call = finished_call(parse_event(_hangup()), _connection())
    assert call is not None
    assert call.direction is CallDirection.INBOUND
    assert (call.customer_phone, call.company_phone) == ("+996555123456", "+996312123456")
    assert call.owner_user_id == MANAGER and call.status is CallStatus.COMPLETED
    assert call.duration_seconds == 290 and call.recording_url
    assert call.external_id == "sipuni:1700000000.42"


def test_outbound_call_uses_the_short_number_and_unknown_extensions_go_to_the_director() -> None:
    event = _hangup(src_num="012345102", src_type="2", dst_num="0555123456", dst_type="1")
    call = finished_call(parse_event(event), _connection())
    assert call is not None and call.direction is CallDirection.OUTBOUND
    assert call.owner_user_id == OTHER
    unknown = finished_call(parse_event({**event, "src_num": "012345199"}), _connection())
    assert unknown is not None and unknown.owner_user_id == DIRECTOR


def test_unanswered_call_is_missed_without_recording() -> None:
    event = _hangup(status="NOANSWER", call_answer_timestamp="0")
    call = finished_call(parse_event(event), _connection())
    assert call is not None
    assert (call.status, call.duration_seconds, call.recording_url) == (CallStatus.MISSED, 0, None)


def test_internal_calls_and_other_events_are_not_logged() -> None:
    connection = _connection()
    assert finished_call(parse_event(_hangup(is_inner_call="1")), connection) is None
    assert finished_call(parse_event(_hangup(src_type="2")), connection) is None
    assert finished_call(parse_event(_hangup(event="1")), connection) is None


def test_malformed_events_are_rejected() -> None:
    with pytest.raises(DomainValidationError):
        parse_event({"event": "9", "call_id": "x"})
    with pytest.raises(DomainValidationError):
        parse_event({**_hangup(), "timestamp": "soon"})


def test_connection_is_validated() -> None:
    with pytest.raises(DomainValidationError):
        _connection(account="abc")
    with pytest.raises(DomainValidationError):
        _connection(extensions={"1": MANAGER})
    with pytest.raises(DomainValidationError):
        _connection(call_language="de")
