"""CSV rows of an import: parsing, normalization and validation (pure Python).

A file is a CSV exported from a spreadsheet (comma, semicolon or tab separated, header
row first). Columns are recognised by their header in Russian or English. Phones must be
in international format (+996…): the CRM stores E.164 and never guesses a country code.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from enum import StrEnum

from shared.domain.errors import DomainValidationError

MAX_ROWS = 2000
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_NAME_LENGTH = 200

_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_HEADERS: dict[str, frozenset[str]] = {
    "name": frozenset({"name", "имя", "фио", "название", "клиент", "контакт", "full name"}),
    "phone": frozenset({"phone", "телефон", "тел", "номер", "мобильный", "phone number"}),
    "email": frozenset({"email", "e-mail", "почта", "эл. почта", "электронная почта", "mail"}),
    "source": frozenset({"source", "источник", "канал"}),
}


class ImportEntity(StrEnum):
    LEADS = "leads"
    CUSTOMERS = "customers"


@dataclass(frozen=True, slots=True)
class ImportRow:
    """One data row after normalization; ``errors`` explain why it cannot be imported."""

    line: int  # line number in the file (the header is line 1)
    name: str
    phone: str | None
    email: str | None
    source: str | None
    errors: tuple[str, ...] = field(default=())

    @property
    def phone_digits(self) -> str | None:
        return self.phone[1:] if self.phone else None


def normalize_phone(raw: str) -> tuple[str | None, str | None]:
    """(E.164 phone, error). Empty input is not an error."""
    value = re.sub(r"[\s\-().]", "", raw or "")
    if not value:
        return None, None
    if value.startswith("00"):
        value = "+" + value[2:]
    if not _PHONE_RE.match(value):
        return None, "Телефон в международном формате, например +996555123456"
    return value, None


def normalize_email(raw: str) -> tuple[str | None, str | None]:
    value = (raw or "").strip().lower()
    if not value:
        return None, None
    if len(value) > 254 or not _EMAIL_RE.match(value):
        return None, "Неверный e-mail"
    return value, None


def _column_map(header: list[str]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, title in enumerate(header):
        key = title.strip().lower().lstrip("﻿")
        for field_name, aliases in _HEADERS.items():
            if key in aliases and field_name not in columns:
                columns[field_name] = index
    return columns


def _dialect(sample: str) -> type[csv.Dialect] | csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def parse_rows(text: str, entity: ImportEntity) -> list[ImportRow]:
    """Rows of the file, each validated for ``entity``. Raises on an unusable file."""
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise DomainValidationError("Файл больше 2 МБ — разделите его на части.")  # noqa: RUF001
    lines = [line for line in text.lstrip("﻿").splitlines() if line.strip()]
    if len(lines) < 2:
        raise DomainValidationError(
            "В файле нет строк с данными: нужен заголовок и хотя бы одна строка."  # noqa: RUF001
        )
    if len(lines) - 1 > MAX_ROWS:
        raise DomainValidationError(f"Больше {MAX_ROWS} строк — разделите файл на части.")
    reader = csv.reader(io.StringIO("\n".join(lines)), _dialect(lines[0]))
    header = next(reader)
    columns = _column_map(header)
    if "name" not in columns:
        raise DomainValidationError("Нет колонки с именем (Имя, ФИО, Название или Name).")  # noqa: RUF001
    if "phone" not in columns and "email" not in columns:
        raise DomainValidationError("Нет колонки «Телефон» или «E-mail».")

    def cell(values: list[str], name: str) -> str:
        index = columns.get(name)
        return values[index].strip() if index is not None and index < len(values) else ""

    rows: list[ImportRow] = []
    for number, values in enumerate(reader, start=2):
        errors: list[str] = []
        name = cell(values, "name")[:MAX_NAME_LENGTH]
        if not name:
            errors.append("Не указано имя")  # noqa: RUF001
        phone, phone_error = normalize_phone(cell(values, "phone"))
        email, email_error = normalize_email(cell(values, "email"))
        errors.extend(e for e in (phone_error, email_error) if e)
        if (
            entity is ImportEntity.LEADS
            and phone is None
            and email is None
            and not (phone_error or email_error)
        ):
            errors.append("Нужен телефон или e-mail")
        source = cell(values, "source") or None
        rows.append(ImportRow(number, name, phone, email, source, tuple(errors)))
    return rows
