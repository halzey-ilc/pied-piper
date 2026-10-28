from __future__ import annotations

from uuid import uuid4

import pytest

from modules.notifications.domain.notification import Notification, NotificationCategory
from modules.notifications.domain.preferences import NotificationPreferences
from shared.domain.errors import DomainValidationError


def test_create_and_mark_read() -> None:
    n = Notification.create(
        recipient_user_id=uuid4(),
        category=NotificationCategory.DEAL,
        title="  New   deal ",
        body="A deal was created.",
    )
    assert n.title == "New deal"
    assert n.mark_read() is True  # was unread
    assert n.read is True
    assert n.read_at is not None
    assert n.mark_read() is False  # already read


def test_title_required() -> None:
    with pytest.raises(DomainValidationError):
        Notification.create(
            recipient_user_id=uuid4(), category=NotificationCategory.SYSTEM, title="   ", body="x"
        )


def test_preferences_default_on_and_toggle() -> None:
    prefs = NotificationPreferences(user_id=uuid4())
    assert prefs.is_enabled(NotificationCategory.MEETING)
    assert prefs.set_category(NotificationCategory.MEETING, enabled=False) is True
    assert not prefs.is_enabled(NotificationCategory.MEETING)
    assert prefs.set_category(NotificationCategory.MEETING, enabled=False) is False  # no change
    assert prefs.set_category(NotificationCategory.MEETING, enabled=True) is True
    assert prefs.is_enabled(NotificationCategory.MEETING)
    assert prefs.version == 3
