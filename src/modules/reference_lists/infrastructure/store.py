from __future__ import annotations

import uuid
from uuid import UUID

from django.db.models import QuerySet

from modules.reference_lists.domain.lists import ListItem, ListKey
from modules.reference_lists.infrastructure.models import ReferenceItemRecord


def _to_item(row: ReferenceItemRecord) -> ListItem:
    return ListItem(
        id=row.id, code=row.code, label=row.label, is_active=row.is_active, position=row.position
    )


class DjangoReferenceListStore:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self, key: ListKey) -> QuerySet[ReferenceItemRecord]:
        return ReferenceItemRecord.objects.using(self.using).filter(list_key=key.value)

    def items(self, key: ListKey) -> list[ListItem]:
        return [_to_item(row) for row in self._qs(key).order_by("position", "created_at")]

    def add(self, key: ListKey, label: str, *, code: str | None, position: int) -> ListItem:
        row = ReferenceItemRecord.objects.using(self.using).create(
            id=uuid.uuid4(), list_key=key.value, code=code, label=label, position=position
        )
        return _to_item(row)

    def update(
        self, key: ListKey, item_id: UUID, *, label: str | None, is_active: bool | None
    ) -> ListItem | None:
        row = self._qs(key).filter(id=item_id).first()
        if row is None:
            return None
        if label is not None:
            row.label = label
        if is_active is not None:
            row.is_active = is_active
        row.save(using=self.using, update_fields=["label", "is_active"])
        return _to_item(row)

    def reorder(self, key: ListKey, item_ids: list[UUID]) -> None:
        for position, item_id in enumerate(item_ids):
            self._qs(key).filter(id=item_id).update(position=position)
