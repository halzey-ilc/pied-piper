# ruff: noqa: RUF001, RUF002, RUF003 - Russian text and patterns by design
"""Contact details are removed from free text before it leaves the CRM for the AI Platform.

Summaries and briefs do not need a client's phone number or e-mail, and managers often paste
them into notes and messages. Only the outgoing text is masked: the CRM keeps the original, and
source fingerprints are taken before masking.

Names are handled by what the CRM knows, not by guessing: the customer or contact of the record
is masked in any grammatical case, and a company with a legal form ("ОсОО «Вымысел»") by its
shape. A name the CRM does not know (a person only mentioned in a note) stays in the text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

PHONE_MASK = "[телефон]"
EMAIL_MASK = "[email]"
CLIENT_MASK = "[клиент]"
COMPANY_MASK = "[компания]"

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


# A legal form and the name after it: ОсОО «Вымысел», ООО Ромашка Плюс, ИП Асанов.
_LEGAL_FORMS = ("ОсОО", "ОАО", "ЗАО", "ООО", "ПАО", "ИП", "ЧП", "LLC")
_COMPANY = re.compile(
    rf"\b(?:{'|'.join(_LEGAL_FORMS)})\s+(?:[«\"“][^»\"”]{{1,80}}[»\"”]|[А-ЯЁA-Z][\w-]+(?:\s+[А-ЯЁA-Z][\w-]+)?)"
)
_MIN_NAME_PART = 3


def redact_contacts(text: str) -> str:
    return _PHONE.sub(PHONE_MASK, _EMAIL.sub(EMAIL_MASK, text))


def _name_pattern(names: Iterable[str]) -> re.Pattern[str] | None:
    parts = {
        part
        for name in names
        for part in re.findall(r"[^\W\d_]+", name)
        if len(part) >= _MIN_NAME_PART and part not in _LEGAL_FORMS
    }
    if not parts:
        return None
    # The last letter may change with the case: Айдана, Айданы, Айданой.
    stems = sorted({p[:-1] if len(p) > _MIN_NAME_PART + 1 else p for p in parts}, key=len)
    alternatives = "|".join(re.escape(stem) for stem in reversed(stems))
    return re.compile(rf"\b(?:{alternatives})[^\W\d_]{{0,3}}\b", re.IGNORECASE)


def mask_personal_data(text: str, names: Iterable[str] = ()) -> str:
    """Contacts, companies with a legal form and the record's known names."""
    masked = _COMPANY.sub(COMPANY_MASK, redact_contacts(text))
    pattern = _name_pattern(names)
    return masked if pattern is None else pattern.sub(CLIENT_MASK, masked)
