from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from modules.telephony.domain.call import (
    Call,
    CallDirection,
    CallEnded,
    CallStarted,
    CallStateError,
    CallStatus,
)
from shared.domain.errors import DomainValidationError

WHEN = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)


def test_log_completed_call() -> None:
    call, event = Call.log(
        direction=CallDirection.INBOUND,
        from_number="+77011112233",
        to_number="+77019998877",
        owner_user_id=uuid4(),
        status=CallStatus.COMPLETED,
        started_at=WHEN,
        duration_seconds=125,
        recording_url="https://rec.example.test/abc",
    )
    assert call.status.value == "completed"
    assert str(call.from_number) == "+77011112233"
    assert isinstance(event, CallEnded) and event.payload()["duration_seconds"] == 125


def test_log_requires_terminal_status_and_valid_numbers() -> None:
    with pytest.raises(DomainValidationError):
        Call.log(
            direction=CallDirection.INBOUND,
            from_number="+77011112233",
            to_number="+77019998877",
            owner_user_id=uuid4(),
            status=CallStatus.RINGING,
            started_at=WHEN,
        )
    with pytest.raises(DomainValidationError):
        Call.log(
            direction=CallDirection.INBOUND,
            from_number="12345",
            to_number="+77019998877",
            owner_user_id=uuid4(),
            status=CallStatus.COMPLETED,
            started_at=WHEN,
        )


def test_click_to_call_lifecycle() -> None:
    call, started = Call.request(
        from_number="+77011112233", to_number="+77019998877", owner_user_id=uuid4()
    )
    assert isinstance(started, CallStarted)
    assert call.direction.value == "outbound" and call.status.value == "requested"
    call.advance(CallStatus.RINGING)
    call.advance(CallStatus.IN_PROGRESS)
    assert call.status.value == "in_progress" and call.started_at is not None
    event = call.end(CallStatus.COMPLETED, duration_seconds=90)
    assert isinstance(event, CallEnded)
    assert call.status.value == "completed" and call.ended_at is not None


def test_advance_rules() -> None:
    call, _ = Call.request(
        from_number="+77011112233", to_number="+77019998877", owner_user_id=uuid4()
    )
    with pytest.raises(CallStateError):  # cannot skip backwards / same
        call.advance(CallStatus.REQUESTED)
    with pytest.raises(CallStateError):  # cannot advance to terminal
        call.advance(CallStatus.COMPLETED)
    call.end(CallStatus.MISSED)
    with pytest.raises(CallStateError):  # already ended
        call.advance(CallStatus.RINGING)
    with pytest.raises(CallStateError):
        call.end(CallStatus.COMPLETED)
