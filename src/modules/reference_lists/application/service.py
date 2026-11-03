"""Reference-list use cases. Every member of the company reads the lists (they feed the
forms); only someone who manages company settings (the director) changes them."""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.reference_lists.application.ports import ReferenceListStore
from modules.reference_lists.domain.lists import (
    LEAD_SOURCE_LABELS,
    MAX_ITEMS,
    ListItem,
    ListKey,
    clean_label,
)
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, DomainValidationError, NotFoundError


class ReferenceListService:
    def __init__(
        self,
        store: ReferenceListStore,
        uow: UnitOfWork,
        audit: AuditLog,
        *,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._store = store
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def can_manage(self, principal: Principal) -> bool:
        return self._policy.allows(principal, Permission.COMPANY_SETTINGS_MANAGE)

    def items(self, key: ListKey, *, active_only: bool = False) -> list[ListItem]:
        stored = self._store.items(key)
        if key.is_fixed:
            by_code = {item.code: item for item in stored if item.code}
            merged = [
                by_code.get(code)
                or ListItem(id=None, code=code, label=label, is_active=True, position=index)
                for index, (code, label) in enumerate(LEAD_SOURCE_LABELS.items())
            ]
            result = sorted(merged, key=lambda item: item.position)
        else:
            result = stored
        return [item for item in result if item.is_active] if active_only else result

    def add(self, principal: Principal, key: ListKey, label: str) -> ListItem:
        self._require(principal)
        if key.is_fixed:
            raise DomainValidationError("Lead sources are fixed: rename or switch them off.")
        cleaned = clean_label(label)
        with self._uow:
            current = self._store.items(key)
            if len(current) >= MAX_ITEMS:
                raise DomainValidationError(f"A list can have at most {MAX_ITEMS} items.")
            if any(item.label.casefold() == cleaned.casefold() for item in current):
                raise ConflictError("This reason is already in the list.")
            item = self._store.add(key, cleaned, code=None, position=len(current))
            self._record(principal, "reference_list.item_added", key, item, ("label",))
        return item

    def update(
        self,
        principal: Principal,
        key: ListKey,
        item_ref: str,
        *,
        label: str | None = None,
        is_active: bool | None = None,
    ) -> ListItem:
        self._require(principal)
        cleaned = clean_label(label) if label is not None else None
        with self._uow:
            if key.is_fixed:
                item = self._fixed_item(key, item_ref)
            else:
                item = self._free_item(key, item_ref, cleaned)
            updated = (
                self._store.update(key, item.id, label=cleaned, is_active=is_active)
                if item.id is not None
                else None
            )
            if updated is None:
                raise NotFoundError("No such item in the list.")
            changed = tuple(
                name
                for name, value in (("label", cleaned), ("is_active", is_active))
                if value is not None
            )
            self._record(principal, "reference_list.item_updated", key, updated, changed)
        return updated

    def reorder(self, principal: Principal, key: ListKey, item_ids: list[UUID]) -> list[ListItem]:
        self._require(principal)
        if key.is_fixed:
            raise DomainValidationError("Lead sources keep their order.")
        with self._uow:
            known = {item.id for item in self._store.items(key)}
            if set(item_ids) != known or len(item_ids) != len(known):
                raise DomainValidationError("Send every item of the list exactly once.")
            self._store.reorder(key, item_ids)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="reference_list.reordered",
                    resource_type="reference_list",
                    resource_id=key.value,
                    changed_fields=("position",),
                )
            )
        return self._store.items(key)

    # --- helpers ------------------------------------------------------------

    def _fixed_item(self, key: ListKey, code: str) -> ListItem:
        if code not in LEAD_SOURCE_LABELS:
            raise NotFoundError("No such lead source.")
        stored = next((i for i in self._store.items(key) if i.code == code), None)
        if stored is not None:
            return stored
        # The first change to a fixed item stores it with its default label and position.
        position = list(LEAD_SOURCE_LABELS).index(code)
        return self._store.add(key, LEAD_SOURCE_LABELS[code], code=code, position=position)

    def _free_item(self, key: ListKey, item_ref: str, new_label: str | None) -> ListItem:
        try:
            item_id = UUID(item_ref)
        except ValueError as exc:
            raise NotFoundError("No such item in the list.") from exc
        current = self._store.items(key)
        item = next((i for i in current if i.id == item_id), None)
        if item is None:
            raise NotFoundError("No such item in the list.")
        if new_label is not None and any(
            other.id != item_id and other.label.casefold() == new_label.casefold()
            for other in current
        ):
            raise ConflictError("This reason is already in the list.")
        return item

    def _require(self, principal: Principal) -> None:
        self._policy.require(principal, Permission.COMPANY_SETTINGS_MANAGE)

    def _record(
        self,
        principal: Principal,
        action: str,
        key: ListKey,
        item: ListItem,
        changed: tuple[str, ...],
    ) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type="reference_list",
                resource_id=f"{key.value}:{item.code or item.id}",
                changed_fields=changed,
            )
        )
