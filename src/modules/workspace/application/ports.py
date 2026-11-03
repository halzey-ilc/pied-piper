from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from modules.workspace.domain.layout import Layout


@dataclass(frozen=True, slots=True)
class StoredLayout:
    layout: Layout
    department_id: UUID | None  # None — the company-wide layout
    updated_by: UUID | None
    updated_at: datetime | None


class LayoutStore(Protocol):
    def load(self, department_id: UUID | None) -> StoredLayout | None: ...

    def save(
        self, department_id: UUID | None, layout: Layout, *, updated_by: UUID
    ) -> StoredLayout: ...


class DepartmentDirectory(Protocol):
    def exists(self, tenant_id: UUID, department_id: UUID) -> bool: ...
