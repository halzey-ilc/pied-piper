"""Custom field definitions and value validation. Pure Python: no Django, no ORM.

A company defines extra typed fields on deals; values live in the deal's
``custom_fields`` map and are validated against the active definitions. Definitions
are deactivated, never deleted, so stored values keep their meaning.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError

MAX_LABEL_LENGTH = 100
MAX_KEY_LENGTH = 50
MAX_TEXT_VALUE_LENGTH = 2000
MAX_OPTIONS = 50
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")


class CustomFieldType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    MONEY = "money"
    URL = "url"


_SELECT_TYPES = {CustomFieldType.SINGLE_SELECT, CustomFieldType.MULTI_SELECT}


def _clean_label(label: str) -> str:
    cleaned = " ".join(label.split())
    if not cleaned:
        raise DomainValidationError("Custom field label must not be empty.")
    if len(cleaned) > MAX_LABEL_LENGTH:
        raise DomainValidationError(f"Label must be at most {MAX_LABEL_LENGTH} characters.")
    return cleaned


def _clean_options(field_type: CustomFieldType, options: Iterable[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in options:
        value = " ".join(raw.split())
        if value and value not in seen:
            seen.add(value)
            cleaned.append(value)
    if field_type in _SELECT_TYPES:
        if not cleaned:
            raise DomainValidationError("A select field needs at least one option.")
        if len(cleaned) > MAX_OPTIONS:
            raise DomainValidationError(f"A select field can have at most {MAX_OPTIONS} options.")
    elif cleaned:
        raise DomainValidationError("Only select fields may have options.")
    return tuple(cleaned)


@dataclass(slots=True)
class CustomFieldDefinition:
    id: UUID
    key: str
    label: str
    field_type: CustomFieldType
    options: tuple[str, ...] = ()
    required: bool = False
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        *,
        key: str,
        label: str,
        field_type: CustomFieldType,
        options: Iterable[str] = (),
        required: bool = False,
    ) -> CustomFieldDefinition:
        normalized_key = key.strip().lower()
        if not _KEY_RE.match(normalized_key):
            raise DomainValidationError(
                "Key must start with a letter and use only lowercase letters, digits and "
                f"underscores (at most {MAX_KEY_LENGTH} characters)."
            )
        return cls(
            id=uuid4(),
            key=normalized_key,
            label=_clean_label(label),
            field_type=field_type,
            options=_clean_options(field_type, options),
            required=required,
        )

    def relabel(self, label: str) -> bool:
        cleaned = _clean_label(label)
        if cleaned == self.label:
            return False
        self.label = cleaned
        return True

    def set_options(self, options: Iterable[str]) -> bool:
        cleaned = _clean_options(self.field_type, options)
        if cleaned == self.options:
            return False
        self.options = cleaned
        return True

    def set_required(self, *, required: bool) -> bool:
        if self.required == required:
            return False
        self.required = required
        return True

    def set_active(self, *, is_active: bool) -> bool:
        if self.is_active == is_active:
            return False
        self.is_active = is_active
        return True


def _v_text(d: CustomFieldDefinition, value: Any) -> Any:
    if not isinstance(value, str):
        raise DomainValidationError(f"'{d.label}' must be text.")
    if len(value) > MAX_TEXT_VALUE_LENGTH:
        raise DomainValidationError(f"'{d.label}' is too long.")
    return value


def _v_url(d: CustomFieldDefinition, value: Any) -> Any:
    text = _v_text(d, value)
    if "://" not in text:
        raise DomainValidationError(f"'{d.label}' must be a URL.")
    return text


def _v_number(d: CustomFieldDefinition, value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DomainValidationError(f"'{d.label}' must be a number.")
    return value


def _v_money(d: CustomFieldDefinition, value: Any) -> Any:
    try:
        return str(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DomainValidationError(f"'{d.label}' must be a money amount.") from exc


def _v_boolean(d: CustomFieldDefinition, value: Any) -> Any:
    if not isinstance(value, bool):
        raise DomainValidationError(f"'{d.label}' must be true or false.")
    return value


def _v_date(d: CustomFieldDefinition, value: Any) -> Any:
    try:
        date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise DomainValidationError(f"'{d.label}' must be a date (YYYY-MM-DD).") from exc
    return value


def _v_datetime(d: CustomFieldDefinition, value: Any) -> Any:
    try:
        datetime.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise DomainValidationError(f"'{d.label}' must be an ISO 8601 datetime.") from exc
    return value


def _v_single_select(d: CustomFieldDefinition, value: Any) -> Any:
    if value not in d.options:
        raise DomainValidationError(f"'{d.label}' must be one of its options.")
    return value


def _v_multi_select(d: CustomFieldDefinition, value: Any) -> Any:
    if not isinstance(value, list) or any(item not in d.options for item in value):
        raise DomainValidationError(f"'{d.label}' must be a list of its options.")
    return value


_VALIDATORS: Mapping[CustomFieldType, Callable[[CustomFieldDefinition, Any], Any]] = {
    CustomFieldType.TEXT: _v_text,
    CustomFieldType.URL: _v_url,
    CustomFieldType.NUMBER: _v_number,
    CustomFieldType.MONEY: _v_money,
    CustomFieldType.BOOLEAN: _v_boolean,
    CustomFieldType.DATE: _v_date,
    CustomFieldType.DATETIME: _v_datetime,
    CustomFieldType.SINGLE_SELECT: _v_single_select,
    CustomFieldType.MULTI_SELECT: _v_multi_select,
}


def _validate_value(definition: CustomFieldDefinition, value: Any) -> Any:
    """Return the value to store, normalized where needed, or raise if it does not
    match the definition's type."""
    return _VALIDATORS[definition.field_type](definition, value)


def validate_custom_fields(
    definitions: Iterable[CustomFieldDefinition],
    values: Mapping[str, Any],
    *,
    require_all: bool = True,
) -> dict[str, Any]:
    """Validate a full custom-field map against the active definitions (full
    replace). Unknown keys are rejected; required fields must be present when
    ``require_all``; each value must match its definition's type."""
    active = {d.key: d for d in definitions if d.is_active}
    unknown = set(values) - set(active)
    if unknown:
        raise DomainValidationError(f"Unknown custom field(s): {', '.join(sorted(unknown))}.")
    if require_all:
        missing = [d.key for d in active.values() if d.required and d.key not in values]
        if missing:
            raise DomainValidationError(f"Missing required field(s): {', '.join(sorted(missing))}.")
    return {key: _validate_value(active[key], value) for key, value in values.items()}
