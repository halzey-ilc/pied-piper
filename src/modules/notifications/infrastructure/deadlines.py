"""Adapters for task deadline alerts: open tasks from the company's database and the head
of sales of a user's department from the control plane."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from modules.access_control.domain.rbac import Role
from modules.activities.infrastructure.models import ActivityRecord
from modules.customers.infrastructure.models import CustomerRecord
from modules.notifications.domain.deadlines import TaskDeadline
from modules.organizations.infrastructure.models import Membership
from modules.sales_pipeline.infrastructure.models import DealRecord

# "manager" is the stored legacy value of the head of sales
_HEAD_ROLES = (Role.HEAD_OF_SALES.value, Role.MANAGER.value)


class DjangoTaskDeadlineSource:
    def __init__(self, using: str) -> None:
        self.using = using

    def open_tasks_due_until(self, until: date) -> list[TaskDeadline]:
        rows = (
            ActivityRecord.objects.using(self.using)
            .filter(
                kind="task",
                task_status="open",
                due_date__isnull=False,
                due_date__lte=until,
                assignee_user_id__isnull=False,
            )
            .order_by("due_date", "id")
            .values("id", "title", "assignee_user_id", "due_date", "subject_type", "subject_id")
        )
        deadlines = []
        for row in rows:
            assignee, due = row["assignee_user_id"], row["due_date"]
            if assignee is None or due is None:  # excluded by the filter; keeps types exact
                continue
            deadlines.append(
                TaskDeadline(
                    task_id=row["id"],
                    title=row["title"] or "",
                    assignee_user_id=assignee,
                    due_date=due,
                    subject_type=row["subject_type"],
                    subject_id=row["subject_id"],
                )
            )
        return deadlines

    def subject_titles(self, tasks: list[TaskDeadline]) -> dict[UUID, str]:
        deal_ids = {t.subject_id for t in tasks if t.subject_type == "deal"}
        customer_ids = {t.subject_id for t in tasks if t.subject_type == "customer"}
        titles: dict[UUID, str] = dict(
            DealRecord.objects.using(self.using).filter(id__in=deal_ids).values_list("id", "title")
        )
        titles.update(
            CustomerRecord.objects.using(self.using)
            .filter(id__in=customer_ids)
            .values_list("id", "name")
        )
        return titles


class DjangoDepartmentHeads:
    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id

    def head_of(self, user_id: UUID) -> UUID | None:
        department_id = (
            Membership.objects.filter(
                organization_id=self.tenant_id, user_id=user_id, is_active=True
            )
            .values_list("department_id", flat=True)
            .first()
        )
        if department_id is None:
            return None
        head: UUID | None = (
            Membership.objects.filter(
                organization_id=self.tenant_id,
                department_id=department_id,
                role__in=_HEAD_ROLES,
                is_active=True,
                user__is_active=True,
            )
            .values_list("user_id", flat=True)
            .first()
        )
        return head
