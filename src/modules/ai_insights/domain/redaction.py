"""Contact details are removed from free text before it leaves the CRM for the AI Platform.

Summaries and briefs do not need a client's phone number or e-mail, and managers often paste
them into notes and messages. Only the outgoing text is masked: the CRM keeps the original, and
source fingerprints are taken before masking. Names are not recognised here (that needs more
than a pattern); a deal title or a note may still contain one.
"""

from __future__ import annotations

import re

PHONE_MASK = "[телефон]"
EMAIL_MASK = "[email]"

_SEP = r"[\s().-]*"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _digits(count: int) -> str:
    return rf"(?:{_SEP}\d){{{count}}}"


# International (+...), then the local shapes of the region: 996 + 9, 7/8 + 10, 0 + 9 digits.
# Tied to these prefixes so that amounts and dates such as "2026-11-05 10:30" stay intact.
_PHONE = re.compile(
    r"(?<![\w+])(?:"
    rf"\+\d{_digits(8)}(?:{_SEP}\d){{0,6}}"
    rf"|996{_digits(9)}"
    rf"|[78]{_digits(10)}"
    rf"|0{_digits(9)}"
    r")(?!\w)"
)


def redact_contacts(text: str) -> str:
    return _PHONE.sub(PHONE_MASK, _EMAIL.sub(EMAIL_MASK, text))
