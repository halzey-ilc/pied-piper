from __future__ import annotations

from uuid import UUID

from django.db.models import QuerySet

from modules.communications.domain.template import MessageTemplate
from modules.communications.infrastructure.models import MessageTemplateRecord


def _to_domain(row: MessageTemplateRecord) -> MessageTemplate:
    return MessageTemplate(
        id=row.id,
        title=row.title,
        body=row.body,
        created_by=row.created_by,
        updated_at=row.updated_at,
        created_at=row.created_at,
    )


class DjangoTemplateRepository:
    def __init__(self, using: str) -> None:
        self.using = using

    def _qs(self) -> QuerySet[MessageTemplateRecord]:
        return MessageTemplateRecord.objects.using(self.using)

    def list_all(self) -> list[MessageTemplate]:
        return [_to_domain(row) for row in self._qs().order_by("title", "created_at")]

    def get(self, template_id: UUID) -> MessageTemplate | None:
        row = self._qs().filter(id=template_id).first()
        return None if row is None else _to_domain(row)

    def add(self, template: MessageTemplate) -> None:
        self._qs().create(
            id=template.id,
            title=template.title,
            body=template.body,
            created_by=template.created_by,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )

    def save(self, template: MessageTemplate) -> None:
        self._qs().filter(id=template.id).update(
            title=template.title, body=template.body, updated_at=template.updated_at
        )

    def delete(self, template_id: UUID) -> None:
        self._qs().filter(id=template_id).delete()
