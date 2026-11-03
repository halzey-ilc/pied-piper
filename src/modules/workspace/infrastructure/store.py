from __future__ import annotations

from uuid import UUID

from modules.organizations.infrastructure.models import Department
from modules.workspace.application.ports import StoredLayout
from modules.workspace.domain.layout import Layout
from modules.workspace.infrastructure.models import COMPANY_SCOPE, WorkspaceLayoutRecord


def _key(department_id: UUID | None) -> str:
    return str(department_id) if department_id is not None else COMPANY_SCOPE


def _to_stored(row: WorkspaceLayoutRecord) -> StoredLayout:
    layout = Layout.of([(str(b["id"]), bool(b["visible"])) for b in row.blocks if b.get("id")])
    return StoredLayout(
        layout=layout,
        department_id=row.department_id,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


class DjangoLayoutStore:
    def __init__(self, using: str) -> None:
        self.using = using

    def load(self, department_id: UUID | None) -> StoredLayout | None:
        row = (
            WorkspaceLayoutRecord.objects.using(self.using)
            .filter(scope_key=_key(department_id))
            .first()
        )
        return _to_stored(row) if row is not None else None

    def save(self, department_id: UUID | None, layout: Layout, *, updated_by: UUID) -> StoredLayout:
        row, _ = WorkspaceLayoutRecord.objects.using(self.using).update_or_create(
            scope_key=_key(department_id),
            defaults={
                "department_id": department_id,
                "blocks": [{"id": b.id, "visible": b.visible} for b in layout.blocks],
                "updated_by": updated_by,
            },
        )
        return _to_stored(row)


class DjangoDepartmentDirectory:
    """Departments live on the control plane (organizations)."""

    def exists(self, tenant_id: UUID, department_id: UUID) -> bool:
        return (
            Department.objects.using("default")
            .filter(organization_id=tenant_id, id=department_id, is_active=True)
            .exists()
        )
