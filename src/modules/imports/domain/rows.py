"""CSV rows of an import: parsing, normalization and validation (pure Python).

A file is a CSV exported from a spreadsheet (comma, semicolon or tab separated, header
row first). Columns are recognised by their header in Russian or English. Phones must be
in international format (+996…): the CRM stores E.164 and never guesses a country code.
Deals have their own columns (``parse_deal_rows``); names of pipelines, stages, owners and
loss reasons are only read here — the import service resolves them against the company.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
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
    DEALS = "deals"


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


def _column_map(header: list[str], headers: dict[str, frozenset[str]] = _HEADERS) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, title in enumerate(header):
        key = " ".join(title.strip().lower().lstrip("\ufeff").split())
        for field_name, aliases in headers.items():
            if key in aliases and field_name not in columns:
                columns[field_name] = index
    return columns


def _dialect(sample: str) -> type[csv.Dialect] | csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def _read_table(text: str) -> tuple[list[str], Iterator[list[str]]]:
    """Header and data rows of the file. Raises on an unusable file."""
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise DomainValidationError("Файл больше 2 МБ — разделите его на части.")  # noqa: RUF001
    lines = [line for line in text.lstrip("\ufeff").splitlines() if line.strip()]
    if len(lines) < 2:
        raise DomainValidationError(
            "В файле нет строк с данными: нужен заголовок и хотя бы одна строка."  # noqa: RUF001
        )
    if len(lines) - 1 > MAX_ROWS:
        raise DomainValidationError(f"Больше {MAX_ROWS} строк — разделите файл на части.")
    reader = csv.reader(io.StringIO("\n".join(lines)), _dialect(lines[0]))
    return next(reader), reader


def parse_rows(text: str, entity: ImportEntity) -> list[ImportRow]:
    """Rows of the file, each validated for ``entity``. Raises on an unusable file."""
    header, reader = _read_table(text)
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


# --- deals ------------------------------------------------------------------

MAX_TITLE_LENGTH = 200
MAX_SOURCE_LENGTH = 100
MAX_REASON_LENGTH = 500
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")

_DEAL_HEADERS: dict[str, frozenset[str]] = {
    "title": frozenset({"title", "deal", "deal title", "сделка", "название сделки", "название"}),
    "amount": frozenset({"amount", "сумма", "бюджет", "сумма сделки"}),
    "currency": frozenset({"currency", "валюта"}),
    "pipeline": frozenset({"pipeline", "воронка"}),
    "stage": frozenset({"stage", "этап", "стадия"}),
    "owner": frozenset(
        {"owner", "ответственный", "менеджер", "логин ответственного", "owner login"}
    ),
    "customer": frozenset(
        {"customer", "client", "customer name", "клиент", "имя клиента", "контакт", "имя", "фио"}
    ),
    "phone": _HEADERS["phone"],
    "email": _HEADERS["email"],
    "expected_close_date": frozenset(
        {
            "expected close date",
            "close date",
            "дата закрытия",
            "ожидаемая дата закрытия",
            "плановая дата закрытия",
        }
    ),
    "source": _HEADERS["source"],
    "next_step": frozenset({"next step", "следующий шаг", "шаг"}),
    "next_step_due": frozenset(
        {"next step due", "next step date", "срок шага", "срок следующего шага", "дата шага"}
    ),
    "loss_reason": frozenset(
        {"loss reason", "lost reason", "причина проигрыша", "причина отказа", "причина"}
    ),
}


@dataclass(frozen=True, slots=True)
class DealImportRow:
    """One deal row as written in the file; names are resolved later by the service."""

    line: int
    title: str
    amount: Decimal | None = None
    currency: str | None = None
    pipeline: str | None = None
    stage: str | None = None
    owner: str | None = None  # login or e-mail of a member
    customer_name: str | None = None
    phone: str | None = None
    email: str | None = None
    expected_close_date: date | None = None
    source: str | None = None
    next_step: str | None = None
    next_step_due: date | None = None
    loss_reason: str | None = None
    errors: tuple[str, ...] = field(default=())

    @property
    def name(self) -> str:
        return self.title

    @property
    def has_customer(self) -> bool:
        return bool(self.customer_name or self.phone or self.email)


def parse_amount(raw: str) -> tuple[Decimal | None, str | None]:
    """«1 200,50», «1200.5», «1,200.50» -> Decimal. Empty input is not an error."""
    value = re.sub(r"[\s\u00a0']", "", raw or "")
    if not value:
        return None, None
    if "," in value and "." in value:
        value = value.replace(",", "")
    value = value.replace(",", ".")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None, "Сумма — число, например 15000 или 15000,50"
    if not amount.is_finite() or amount < 0:
        return None, "Сумма не может быть отрицательной"
    return amount, None


def parse_date(raw: str, label: str) -> tuple[date | None, str | None]:
    value = (raw or "").strip()
    if not value:
        return None, None
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(value, pattern).date(), None  # noqa: DTZ007 - a calendar date
        except ValueError:
            continue
    return None, f"{label}: дата в формате ДД.ММ.ГГГГ или ГГГГ-ММ-ДД"  # noqa: RUF001


def parse_deal_rows(text: str) -> list[DealImportRow]:
    """Deal rows of the file with the errors found in the row itself. Raises on an
    unusable file (no «Название сделки» column)."""
    header, reader = _read_table(text)
    columns = _column_map(header, _DEAL_HEADERS)
    if "title" not in columns:
        raise DomainValidationError(
            "Нет колонки с названием сделки (Сделка, Название сделки или Title)."  # noqa: RUF001
        )

    def cell(values: list[str], name: str) -> str:
        index = columns.get(name)
        return values[index].strip() if index is not None and index < len(values) else ""

    rows: list[DealImportRow] = []
    for number, values in enumerate(reader, start=2):
        errors: list[str] = []
        title = " ".join(cell(values, "title").split())
        if not title:
            errors.append("Не указано название сделки")  # noqa: RUF001
        elif len(title) > MAX_TITLE_LENGTH:
            errors.append(f"Название сделки длиннее {MAX_TITLE_LENGTH} символов")
        amount, amount_error = parse_amount(cell(values, "amount"))
        currency = cell(values, "currency").upper() or None
        currency_error = (
            "Валюта — трёхбуквенный код, например KGS или USD"
            if currency and not _CURRENCY_RE.match(currency)
            else None
        )
        phone, phone_error = normalize_phone(cell(values, "phone"))
        email, email_error = normalize_email(cell(values, "email"))
        customer_name = cell(values, "customer")[:MAX_NAME_LENGTH] or None
        close_date, close_error = parse_date(cell(values, "expected_close_date"), "Дата закрытия")
        step = cell(values, "next_step")[:MAX_REASON_LENGTH] or None
        step_due, step_error = parse_date(cell(values, "next_step_due"), "Срок шага")
        errors.extend(
            e
            for e in (
                amount_error,
                currency_error,
                phone_error,
                email_error,
                close_error,
                step_error,
            )
            if e
        )
        if customer_name and not (phone or email or phone_error or email_error):
            errors.append("Для клиента укажите телефон или e-mail: по ним ищем его в базе")  # noqa: RUF001
        if (step is None) != (step_due is None) and not step_error:
            errors.append("Следующий шаг: нужны и описание, и срок")
        rows.append(
            DealImportRow(
                line=number,
                title=title,
                amount=amount,
                currency=currency,
                pipeline=" ".join(cell(values, "pipeline").split()) or None,
                stage=" ".join(cell(values, "stage").split()) or None,
                owner=cell(values, "owner").lower() or None,
                customer_name=customer_name,
                phone=phone,
                email=email,
                expected_close_date=close_date,
                source=cell(values, "source")[:MAX_SOURCE_LENGTH] or None,
                next_step=step,
                next_step_due=step_due,
                loss_reason=cell(values, "loss_reason")[:MAX_REASON_LENGTH] or None,
                errors=tuple(errors),
            )
        )
    return rows
