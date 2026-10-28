"""Django SECRET_KEY rotation: old-key signatures still verify via fallbacks."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.signing import BadSignature, Signer

OLD = "old-secret-" + "a" * 48
NEW = "new-secret-" + "b" * 48


def test_value_signed_with_previous_secret_still_verifies(settings: Any) -> None:
    settings.SECRET_KEY = OLD
    signed = Signer().sign("session-value")

    # Rotate: new primary, old kept as a fallback.
    settings.SECRET_KEY = NEW
    settings.SECRET_KEY_FALLBACKS = [OLD]
    assert Signer().unsign(signed) == "session-value"

    # Once the fallback is dropped the old signature is no longer accepted.
    settings.SECRET_KEY_FALLBACKS = []
    with pytest.raises(BadSignature):
        Signer().unsign(signed)
