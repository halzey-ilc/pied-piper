"""Customer/deal existence lookups for the active tenant."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from modules.access_control.domain.rbac import Role
from modules.customers.infrastructure.models import CustomerRecord
from modules.organizations.infrastructure.models import Membership
from modules.payments.application.ports import DealRef
from modules.sales_pipeline.infrastructure.models import DealRecord


class DjangoCustomerLookup:
    def __init__(self, using: str) -> None:
        self.using = using

    def exists(self, customer_id: UUID) -> bool:
        return CustomerRecord.objects.using(self.using).filter(id=customer_id).exists()


class DjangoDealLookup:
    def __init__(self, using: str) -> None:
        self.using = using

    def exists(self, deal_id: UUID) -> bool:
        return DealRecord.objects.using(self.using).filter(id=deal_id).exists()


class DjangoDealValueLookup:
    def __init__(self, using: str) -> None:
        self.using = using

    def values(self, deal_ids: list[UUID]) -> dict[UUID, Decimal]:
        if not deal_ids:
            return {}
        rows = (
            DealRecord.objects.using(self.using).filter(id__in=deal_ids).values_list("id", "amount")
        )
        return {row[0]: row[1] for row in rows}


class DjangoDealRefLookup:
    """Value, currency and ownership of a deal: for the deal card summary and for
    letting everyone who sees a deal see its payments."""

    def __init__(self, using: str) -> None:
        self.using = using

    def find(self, deal_id: UUID) -> DealRef | None:
        row = (
            DealRecord.objects.using(self.using)
            .filter(id=deal_id)
            .values("id", "amount", "currency", "owner_user_id", "team_id", "title")
            .first()
        )
        if row is None:
            return None
        return DealRef(
            id=row["id"],
            amount=row["amount"],
            currency=row["currency"],
            owner_user_id=row["owner_user_id"],
            team_id=row["team_id"],
            title=row["title"],
        )


# Stored values of the director-level roles (owner and admin are legacy aliases)
_DIRECTOR_ROLES = (Role.OWNER.value, Role.DIRECTOR.value, Role.ADMIN.value)


class DjangoDirectorLookup:
    """Directors of the company (control-plane memberships of the tenant)."""

    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id

    def has_other_active_director(self, user_id: UUID) -> bool:
        return (
            Membership.objects.using("default")
            .filter(
                organization_id=self.tenant_id,
                role__in=_DIRECTOR_ROLES,
                is_active=True,
                user__is_active=True,
            )
            .exclude(user_id=user_id)
            .exists()
        )
