"""CrmCounter adapter: grouped counts of leads and customers in the tenant database."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import Count, Q, QuerySet

from modules.customers.infrastructure.models import CustomerRecord
from modules.leads.infrastructure.models import LeadRecord
from modules.sales_kpi.application.crm_counts import CountRow, CountScope, GroupBy

_GROUP_FIELD = {GroupBy.DEPARTMENT: "team_id", GroupBy.USER: "owner_user_id"}


def _scoped[Q_: QuerySet[Any]](qs: Q_, scope: CountScope) -> Q_:
    if scope.owner_user_id is None:
        return qs
    condition = Q(owner_user_id=scope.owner_user_id)
    if scope.team_ids:
        condition |= Q(team_id__in=scope.team_ids)
    return qs.filter(condition)


def _grouped(
    qs: QuerySet[Any], group_by: GroupBy, **aggregates: Any
) -> dict[UUID | None, dict[str, int]]:
    if group_by is GroupBy.COMPANY:
        return {None: qs.aggregate(**aggregates)}
    field = _GROUP_FIELD[group_by]
    return {
        row[field]: {k: row[k] for k in aggregates}
        for row in qs.values(field).annotate(**aggregates).order_by(field)
    }


class DjangoCrmCounter:
    def __init__(self, using: str) -> None:
        self.using = using

    def counts(
        self,
        *,
        leads: CountScope,
        customers: CountScope,
        since: datetime,
        until: datetime,
        group_by: GroupBy,
    ) -> list[CountRow]:
        new = Q(created_at__gte=since, created_at__lt=until)
        lead_rows = _grouped(
            _scoped(LeadRecord.objects.using(self.using), leads),
            group_by,
            total=Count("id"),
            new=Count("id", filter=new),
        )
        customer_rows = _grouped(
            _scoped(CustomerRecord.objects.using(self.using), customers),
            group_by,
            total=Count("id"),
            active=Count("id", filter=Q(archived_at__isnull=True)),
            new=Count("id", filter=new),
        )
        keys = sorted(set(lead_rows) | set(customer_rows), key=lambda k: (k is None, str(k)))
        empty: dict[str, int] = {}
        return [
            CountRow(
                group_id=key,
                leads_total=lead_rows.get(key, empty).get("total") or 0,
                leads_new=lead_rows.get(key, empty).get("new") or 0,
                customers_total=customer_rows.get(key, empty).get("total") or 0,
                customers_active=customer_rows.get(key, empty).get("active") or 0,
                customers_new=customer_rows.get(key, empty).get("new") or 0,
            )
            for key in keys
        ] or [CountRow(None, 0, 0, 0, 0, 0)]
