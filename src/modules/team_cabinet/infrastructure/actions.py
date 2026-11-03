"""Read-only SQL for «Что делать сегодня»: payments to verify, overdue and near-deadline tasks,
meetings without an outcome, deals without a next step or to push, unanswered leads and who
started a shift. Reads other modules' tables in the tenant database; never writes them.

Independent of the operations module's plan gate: only its stalled-deal threshold (a company
setting) is read here, so the cabinet keeps working on every plan.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import (
    CharField,
    Count,
    Exists,
    F,
    Model,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
)
from django.db.models.functions import Cast, Coalesce

from modules.activities.infrastructure.models import ActivityRecord
from modules.audit.infrastructure.models import AuditRecord
from modules.leads.infrastructure.models import LeadRecord
from modules.meetings.infrastructure.models import MeetingRecord
from modules.operations.infrastructure.settings_store import DjangoSettingsStore
from modules.payments.infrastructure.models import PaymentRecord
from modules.planning.infrastructure.models import WorkSessionRecord
from modules.sales_pipeline.infrastructure.models import DealRecord, DealStageChangeRecord
from modules.team_cabinet.domain.actions import ActionGroup, ActionItem, ActionKind, push_reasons
from modules.team_cabinet.infrastructure.analytics import RESPONSE_ACTIONS

# Meeting statuses meaning "the meeting took place" (completed is the legacy value).
_HELD = ("held", "completed")


def _owned[Row: Model](qs: QuerySet[Row], field: str, owners: list[UUID] | None) -> QuerySet[Row]:
    return qs if owners is None else qs.filter(**{f"{field}__in": owners})


def _by_manager(qs: QuerySet[Any], field: str) -> dict[UUID, int]:
    return {
        row[field]: row["n"]
        for row in qs.order_by().values(field).annotate(n=Count("id"))
        if row[field] is not None
    }


def _days(since: datetime | date, now: datetime | date) -> int:
    if isinstance(since, datetime) and isinstance(now, datetime):
        return max((now - since).days, 0)
    since_day = since.date() if isinstance(since, datetime) else since
    now_day = now.date() if isinstance(now, datetime) else now
    return max((now_day - since_day).days, 0)


class DjangoActionsReader:
    def __init__(self, using: str) -> None:
        self.using = using

    def stalled_deal_days(self) -> int:
        return DjangoSettingsStore(self.using).load().stalled_deal_days

    # --- payments ---------------------------------------------------------------

    def payments_to_verify(
        self, owners: list[UUID] | None, *, now: datetime, limit: int
    ) -> ActionGroup:
        qs = _owned(
            PaymentRecord.objects.using(self.using).filter(status="pending_verification"),
            "seller_user_id",
            owners,
        )
        group = ActionGroup(ActionKind.PAYMENTS_TO_VERIFY, count=qs.count())
        group.by_manager = _by_manager(qs, "seller_user_id")
        for row in qs.order_by().values("currency").annotate(total=Sum("amount")):
            group.amounts[row["currency"]] = row["total"] or Decimal(0)
        rows = list(
            qs.order_by("created_at", "id").values(
                "id", "amount", "currency", "paid_on", "created_at", "seller_user_id", "deal_id"
            )[:limit]
        )
        titles = self._deal_titles([r["deal_id"] for r in rows if r["deal_id"]])
        group.items = [
            ActionItem(
                id=r["id"],
                title=titles.get(r["deal_id"], "") if r["deal_id"] else "",
                manager_id=r["seller_user_id"],
                amount=r["amount"],
                currency=r["currency"],
                date=r["paid_on"],
                at=r["created_at"],
                age_days=_days(r["created_at"], now),
                subject_type="deal" if r["deal_id"] else None,
                subject_id=r["deal_id"],
            )
            for r in rows
        ]
        return group

    def _deal_titles(self, ids: list[UUID]) -> dict[UUID, str]:
        if not ids:
            return {}
        return dict(
            DealRecord.objects.using(self.using).filter(id__in=ids).values_list("id", "title")
        )

    # --- tasks ------------------------------------------------------------------

    def _open_tasks(self, owners: list[UUID] | None) -> QuerySet[ActivityRecord]:
        return _owned(
            ActivityRecord.objects.using(self.using).filter(kind="task", task_status="open"),
            "assignee_user_id",
            owners,
        )

    def _task_group(
        self, kind: ActionKind, qs: QuerySet[ActivityRecord], today: date, limit: int
    ) -> ActionGroup:
        group = ActionGroup(kind, count=qs.count())
        group.by_manager = _by_manager(qs, "assignee_user_id")
        rows = qs.order_by("due_date", "created_at", "id").values(
            "id", "title", "due_date", "assignee_user_id", "subject_type", "subject_id"
        )[:limit]
        group.items = [
            ActionItem(
                id=r["id"],
                title=r["title"] or "",
                manager_id=r["assignee_user_id"],
                date=r["due_date"],
                age_days=(
                    _days(r["due_date"], today)
                    if kind is ActionKind.OVERDUE_TASKS and r["due_date"] is not None
                    else None
                ),
                subject_type=r["subject_type"],
                subject_id=r["subject_id"],
            )
            for r in rows
        ]
        return group

    def overdue_tasks(self, owners: list[UUID] | None, *, today: date, limit: int) -> ActionGroup:
        qs = self._open_tasks(owners).filter(due_date__lt=today)
        return self._task_group(ActionKind.OVERDUE_TASKS, qs, today, limit)

    def tasks_due_soon(self, owners: list[UUID] | None, *, today: date, limit: int) -> ActionGroup:
        qs = self._open_tasks(owners).filter(
            due_date__gte=today, due_date__lte=today + timedelta(days=1)
        )
        return self._task_group(ActionKind.TASKS_DUE_SOON, qs, today, limit)

    # --- meetings -----------------------------------------------------------------

    def meetings_without_outcome(
        self, owners: list[UUID] | None, *, now: datetime, limit: int
    ) -> ActionGroup:
        qs = _owned(
            MeetingRecord.objects.using(self.using).filter(
                status="scheduled", scheduled_end__lt=now
            ),
            "owner_user_id",
            owners,
        )
        group = ActionGroup(ActionKind.MEETINGS_WITHOUT_OUTCOME, count=qs.count())
        group.by_manager = _by_manager(qs, "owner_user_id")
        rows = qs.order_by("scheduled_end", "id").values(
            "id",
            "title",
            "owner_user_id",
            "scheduled_start",
            "scheduled_end",
            "subject_type",
            "subject_id",
        )[:limit]
        group.items = [
            ActionItem(
                id=r["id"],
                title=r["title"],
                manager_id=r["owner_user_id"],
                at=r["scheduled_start"],
                age_days=_days(r["scheduled_end"], now),
                subject_type=r["subject_type"],
                subject_id=r["subject_id"],
            )
            for r in rows
        ]
        return group

    # --- deals --------------------------------------------------------------------

    def _open_deals(self, owners: list[UUID] | None) -> QuerySet[DealRecord]:
        return _owned(
            DealRecord.objects.using(self.using).filter(status="open"), "owner_user_id", owners
        )

    def deals_without_next_step(
        self, owners: list[UUID] | None, *, now: datetime, limit: int
    ) -> ActionGroup:
        planned_task = ActivityRecord.objects.using(self.using).filter(
            kind="task",
            task_status="open",
            subject_type="deal",
            subject_id=OuterRef("id"),
            due_date__isnull=False,
        )
        planned_meeting = MeetingRecord.objects.using(self.using).filter(
            subject_type="deal",
            subject_id=OuterRef("id"),
            status="scheduled",
            scheduled_start__gte=now,
        )
        qs = self._open_deals(owners).filter(~Exists(planned_task), ~Exists(planned_meeting))
        group = ActionGroup(ActionKind.DEALS_WITHOUT_NEXT_STEP, count=qs.count())
        group.by_manager = _by_manager(qs, "owner_user_id")
        rows = qs.order_by("updated_at", "id").values(
            "id", "title", "owner_user_id", "amount", "currency", "updated_at", "stage__name"
        )[:limit]
        group.items = [
            ActionItem(
                id=r["id"],
                title=r["title"],
                manager_id=r["owner_user_id"],
                amount=r["amount"],
                currency=r["currency"],
                age_days=_days(r["updated_at"], now),
                subject_type="deal",
                subject_id=r["id"],
                stage_name=r["stage__name"],
            )
            for r in rows
        ]
        return group

    def deals_to_push(
        self, owners: list[UUID] | None, *, now: datetime, stalled_days: int, limit: int
    ) -> ActionGroup:
        latest_move = (
            DealStageChangeRecord.objects.using(self.using)
            .filter(deal_id=OuterRef("id"))
            .order_by("-created_at")
            .values("created_at")[:1]
        )
        candidates = list(
            self._open_deals(owners)
            .annotate(entered=Coalesce(Subquery(latest_move), F("created_at")))
            .filter(entered__lte=now - timedelta(days=stalled_days))
            .order_by("entered", "id")
            .values(
                "id",
                "title",
                "owner_user_id",
                "amount",
                "currency",
                "entered",
                "stage__name",
            )
        )
        ids = [r["id"] for r in candidates]
        held = set(
            MeetingRecord.objects.using(self.using)
            .filter(subject_type="deal", subject_id__in=ids, status__in=_HELD)
            .values_list("subject_id", flat=True)
        )
        # Confirmed money only (``counted``), per deal and currency, refunds subtracted.
        paid: dict[UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        prepaid: set[UUID] = set()
        for p in (
            PaymentRecord.counted.using(self.using)
            .filter(deal_id__in=ids)
            .values("deal_id", "currency", "kind", "is_refund", "amount")
        ):
            if p["deal_id"] is None:  # pragma: no cover - filtered by deal_id__in
                continue
            sign = -1 if p["is_refund"] else 1
            paid[p["deal_id"]][p["currency"]] += sign * p["amount"]
            if p["kind"] == "prepayment" and not p["is_refund"]:
                prepaid.add(p["deal_id"])

        group = ActionGroup(ActionKind.DEALS_TO_PUSH)
        for r in candidates:
            # Only money in the deal's own currency is compared with its amount.
            received = paid.get(r["id"], {}).get(r["currency"], Decimal(0))
            reasons = push_reasons(
                held_meeting=r["id"] in held,
                prepayment=r["id"] in prepaid,
                paid=received,
                amount=r["amount"],
            )
            if not reasons:
                continue
            debt = r["amount"] - received if Decimal(0) < received < r["amount"] else None
            group.count += 1
            owner = r["owner_user_id"]
            group.by_manager[owner] = group.by_manager.get(owner, 0) + 1
            if debt is not None:
                group.amounts[r["currency"]] = group.amounts.get(r["currency"], Decimal(0)) + debt
            if len(group.items) < limit:
                group.items.append(
                    ActionItem(
                        id=r["id"],
                        title=r["title"],
                        manager_id=owner,
                        amount=r["amount"],
                        currency=r["currency"],
                        age_days=_days(r["entered"], now),
                        subject_type="deal",
                        subject_id=r["id"],
                        reasons=reasons,
                        paid=received,
                        debt=debt,
                        stage_name=r["stage__name"],
                    )
                )
        return group

    # --- leads --------------------------------------------------------------------

    def leads_without_contact(
        self, owners: list[UUID] | None, *, now: datetime, response_minutes: int, limit: int
    ) -> ActionGroup:
        # A first response: any activity on the lead or a decision about it by a person
        # (the same definition as the cabinet's lead-response figures).
        activity = ActivityRecord.objects.using(self.using).filter(
            subject_type="lead", subject_id=OuterRef("id")
        )
        decision = AuditRecord.objects.using(self.using).filter(
            resource_type="lead",
            resource_id=Cast(OuterRef("id"), output_field=CharField()),
            action__in=RESPONSE_ACTIONS,
            actor_user_id__isnull=False,
        )
        qs = _owned(
            LeadRecord.objects.using(self.using).filter(
                status="new", created_at__lte=now - timedelta(minutes=response_minutes)
            ),
            "owner_user_id",
            owners,
        ).filter(~Exists(activity), ~Exists(decision))
        group = ActionGroup(ActionKind.LEADS_WITHOUT_CONTACT, count=qs.count())
        group.by_manager = _by_manager(qs, "owner_user_id")
        rows = qs.order_by("created_at", "id").values("id", "name", "owner_user_id", "created_at")[
            :limit
        ]
        group.items = [
            ActionItem(
                id=r["id"],
                title=r["name"],
                manager_id=r["owner_user_id"],
                at=r["created_at"],
                age_days=_days(r["created_at"], now),
                subject_type="lead",
                subject_id=r["id"],
            )
            for r in rows
        ]
        return group

    # --- shifts -------------------------------------------------------------------

    def on_shift(
        self, user_ids: list[UUID], *, day_start: datetime, day_end: datetime
    ) -> set[UUID]:
        if not user_ids:
            return set()
        return set(
            WorkSessionRecord.objects.using(self.using)
            .filter(user_id__in=user_ids)
            .filter(Q(status="open") | Q(started_at__gte=day_start, started_at__lt=day_end))
            .values_list("user_id", flat=True)
        )
