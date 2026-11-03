"""Local phone numbers typed on a website become E.164 for the company's country."""

from __future__ import annotations

import pytest

from modules.web_forms.domain.key import international_phone


@pytest.mark.parametrize(
    ("raw", "country", "expected"),
    [
        ("0700 123 456", "KG", "+996700123456"),
        ("700123456", "KG", "+996700123456"),
        ("996 700 123 456", "KG", "+996700123456"),
        ("+996 (700) 12-34-56", "KG", "+996700123456"),
        ("8 701 123 45 67", "KZ", "+77011234567"),
        ("7 701 123 45 67", "KZ", "+77011234567"),
        ("701 123 45 67", "RU", "+77011234567"),
        ("00996700123456", "RU", "+996700123456"),
        ("12345", "KG", "12345"),
        ("0700123456", "DE", "0700123456"),
    ],
)
def test_international_phone(raw: str, country: str, expected: str) -> None:
    assert international_phone(raw, country) == expected
