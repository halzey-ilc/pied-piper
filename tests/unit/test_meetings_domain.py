from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from modules.meetings.domain.meeting import (
    Meeting,
    MeetingScheduled,
    MeetingStateError,
    Participant,
    ParticipantResponse,
)
from shared.domain.errors import DomainValidationError

START = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
END = START + timedelta(minutes=30)


def _schedule(**over: object) -> Meeting:
    kwargs: dict[str, object] = {
        "title": "Kickoff",
        "owner_user_id": uuid4(),
        "scheduled_start": START,
        "scheduled_end": END,
    }
    kwargs.update(over)
    meeting, _ = Meeting.schedule(**kwargs)  # type: ignore[arg-type]
    return meeting


def test_schedule_validates_window_and_emits_event() -> None:
    owner = uuid4()
    meeting, event = Meeting.schedule(
        title="  Kickoff  call ",
        owner_user_id=owner,
        scheduled_start=START,
        scheduled_end=END,
        reminders=(10, 60),
        participants=(Participant.create(name="Jane", email="j@example.test"),),
    )
    assert meeting.title == "Kickoff call"
    assert meeting.status.value == "scheduled"
    assert meeting.reminders == (60, 10)  # sorted descending
    assert isinstance(event, MeetingScheduled)
    assert event.payload()["owner_user_id"] == str(owner)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (END, START),  # end before start
        (START, START),  # zero length
        (START, START + timedelta(hours=25)),  # too long
    ],
)
def test_invalid_windows_rejected(start: datetime, end: datetime) -> None:
    with pytest.raises(DomainValidationError):
        Meeting.schedule(title="x", owner_user_id=uuid4(), scheduled_start=start, scheduled_end=end)


def test_naive_datetimes_rejected() -> None:
    with pytest.raises(DomainValidationError):
        Meeting.schedule(
            title="x",
            owner_user_id=uuid4(),
            scheduled_start=datetime(2026, 1, 1, 10, 0),  # noqa: DTZ001
            scheduled_end=datetime(2026, 1, 1, 11, 0),  # noqa: DTZ001
        )


def test_reminders_must_be_unique_and_in_range() -> None:
    with pytest.raises(DomainValidationError):
        _schedule(reminders=(10, 10))
    with pytest.raises(DomainValidationError):
        _schedule(reminders=(999999,))


def test_participants_deduped() -> None:
    p1 = Participant.create(name="Jane", email="jane@example.test")
    p2 = Participant.create(name="Different", email="JANE@example.test")  # same e-mail
    with pytest.raises(DomainValidationError):
        _schedule(participants=(p1, p2))


def test_participant_response_and_add_remove() -> None:
    meeting = _schedule(participants=(Participant.create(name="Jane", email="j@example.test"),))
    meeting.add_participant(Participant.create(name="Bob"))
    assert len(meeting.participants) == 2
    meeting.set_response("j@example.test", ParticipantResponse.ACCEPTED)
    jane = next(p for p in meeting.participants if p.email == "j@example.test")
    assert jane.response.value == "accepted"
    meeting.set_response("bob", ParticipantResponse.DECLINED)  # keyed by name
    meeting.remove_participant("bob")
    assert len(meeting.participants) == 1


def test_response_for_unknown_participant_rejected() -> None:
    meeting = _schedule(participants=(Participant.create(name="Jane", email="j@example.test"),))
    with pytest.raises(DomainValidationError):
        meeting.set_response("nobody", ParticipantResponse.ACCEPTED)


def test_lifecycle_complete_and_cancel_are_terminal() -> None:
    meeting = _schedule()
    meeting.complete("went well")
    assert meeting.status.value == "held" and meeting.outcome == "went well"
    with pytest.raises(MeetingStateError):
        meeting.reschedule(START, END)
    with pytest.raises(MeetingStateError):
        meeting.cancel("x")

    other = _schedule()
    other.cancel("client declined")
    assert other.status.value == "cancelled" and other.cancel_reason == "client declined"
    with pytest.raises(MeetingStateError):
        other.complete()


def test_cancel_requires_reason() -> None:
    with pytest.raises(DomainValidationError):
        _schedule().cancel("   ")


def test_reschedule_changes_window() -> None:
    meeting = _schedule()
    new_start = START + timedelta(days=1)
    new_end = new_start + timedelta(hours=1)
    event = meeting.reschedule(new_start, new_end)
    assert meeting.scheduled_start == new_start
    assert event.payload()["scheduled_start"] == new_start.isoformat()
