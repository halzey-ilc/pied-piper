"""Product catalog use cases (company-wide configuration). RBAC enforced here: everyone who
sees pipelines sees the list; the director and the head of sales maintain it."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.sales_pipeline.application.ports import ProductRepository
from modules.sales_pipeline.domain.product import Product
from shared.application.ports import UnitOfWork
from shared.domain.errors import ConflictError, NotFoundError


@dataclass(frozen=True, slots=True)
class UpdateProduct:
    product_id: UUID
    name: str | None = None
    is_active: bool | None = None
    show_on_my_day: bool | None = None


class ProductService:
    def __init__(
        self,
        repository: ProductRepository,
        uow: UnitOfWork,
        audit: AuditLog,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()

    def list_products(self, principal: Principal, *, active_only: bool = False) -> list[Product]:
        self._policy.require(principal, Permission.PIPELINES_VIEW)
        return self._repo.list_all(active_only=active_only)

    def create(self, principal: Principal, name: str, *, show_on_my_day: bool = True) -> Product:
        self._policy.require(principal, Permission.PIPELINES_MANAGE)
        product = Product.create(name=name, show_on_my_day=show_on_my_day)
        with self._uow:
            if self._repo.exists_by_name(product.name):
                raise ConflictError("A product with that name already exists.")
            self._repo.add(product)
            self._record(principal, "product.created", product)
        return product

    def update(self, principal: Principal, command: UpdateProduct) -> Product:
        self._policy.require(principal, Permission.PIPELINES_MANAGE)
        with self._uow:
            product = self._repo.get(command.product_id)
            if product is None:
                raise NotFoundError("Product not found.")
            changed = False
            if command.name is not None:
                cleaned = " ".join(command.name.split())
                if cleaned != product.name and self._repo.exists_by_name(cleaned):
                    raise ConflictError("A product with that name already exists.")
                changed = product.rename(command.name) or changed
            if command.is_active is not None:
                changed = product.set_active(is_active=command.is_active) or changed
            if command.show_on_my_day is not None:
                changed = product.set_shown(show_on_my_day=command.show_on_my_day) or changed
            if changed:
                self._repo.save(product)
                self._record(principal, "product.updated", product)
        return product

    def _record(self, principal: Principal, action: str, product: Product) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=principal.user_id,
                action=action,
                resource_type="product",
                resource_id=str(product.id),
            )
        )
