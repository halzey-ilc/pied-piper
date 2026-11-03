from __future__ import annotations

from typing import Protocol
from uuid import UUID

from modules.reference_lists.domain.lists import ListItem, ListKey


class ReferenceListStore(Protocol):
    def items(self, key: ListKey) -> list[ListItem]:
        """Stored items of a list, ordered by position."""
        ...

    def add(self, key: ListKey, label: str, *, code: str | None, position: int) -> ListItem: ...

    def update(
        self, key: ListKey, item_id: UUID, *, label: str | None, is_active: bool | None
    ) -> ListItem | None: ...

    def reorder(self, key: ListKey, item_ids: list[UUID]) -> None: ...
