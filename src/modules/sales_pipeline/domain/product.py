"""Product catalog entry: what the company sells (an employment programme in Poland, study in
Italy, an SMM course...). Pure Python: no Django, no ORM.

A deal points at one product; a manager's «Мой день» shows a block per product with what they
sold of it this month. The director or the head of sales keeps the list and chooses which
products get a block (``show_on_my_day``). Entries are switched off, never deleted, so old
deals keep pointing at a valid product.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError

MAX_PRODUCT_NAME_LENGTH = 100


def _clean(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise DomainValidationError("Product name must not be empty.")
    if len(cleaned) > MAX_PRODUCT_NAME_LENGTH:
        raise DomainValidationError(
            f"Product name must be at most {MAX_PRODUCT_NAME_LENGTH} characters."
        )
    return cleaned


@dataclass(slots=True)
class Product:
    id: UUID
    name: str
    is_active: bool = True
    show_on_my_day: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(cls, *, name: str, show_on_my_day: bool = True) -> Product:
        return cls(id=uuid4(), name=_clean(name), show_on_my_day=show_on_my_day)

    def rename(self, name: str) -> bool:
        cleaned = _clean(name)
        if cleaned == self.name:
            return False
        self.name = cleaned
        return True

    def set_active(self, *, is_active: bool) -> bool:
        if self.is_active == is_active:
            return False
        self.is_active = is_active
        return True

    def set_shown(self, *, show_on_my_day: bool) -> bool:
        if self.show_on_my_day == show_on_my_day:
            return False
        self.show_on_my_day = show_on_my_day
        return True
