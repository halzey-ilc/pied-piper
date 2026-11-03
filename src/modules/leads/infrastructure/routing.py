"""LeadRouting adapter: rules and rotation pointer in the tenant database, members on the
control plane, shifts and absences from planning, alerts through notifications."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import QuerySet

from modules.access_control.domain.rbac import Role
from modules.leads.domain.lead import Lead
from modules.leads.domain.routing import RoutingRule
from modules.leads.infrastructure.models import LeadRoutingRuleRecord
from modules.notifications.domain.notification import NotificationCategory
from modules.organizations.infrastructure.models import Department, Membership, Organization
from modules.planning.domain.work_session import WorkSessionStatus
from modules.planning.infrastructure.models import AbsenceRecord, WorkSessionRecord

_HEAD_ROLES = (Role.HEAD_OF_SALES.value, Role.MANAGER.value)
_DEFAULT_TIMEZONE = "Asia/Bishkek"  # as organizations.company_settings


def _to_rule(row: LeadRoutingRuleRecord) -> RoutingRule:
    return RoutingRule(
        department_id=row.department_id,
        enabled=row.enabled,
        member_ids=frozenset(UUID(str(m)) for m in row.member_ids),
        require_on_shift=row.require_on_shift,
        skip_on_absence=row.skip_on_absence,
        is_company_intake=row.is_company_intake,
        last_assigned_user_id=row.last_assigned_user_id,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


class DjangoLeadRouting:
    def __init__(self, using: str, tenant_id: UUID) -> None:
        self.using = using
        self.tenant_id = tenant_id

    def _rules(self) -> QuerySet[LeadRoutingRuleRecord]:
        return LeadRoutingRuleRecord.objects.using(self.using)

    def rule(self, department_id: UUID) -> RoutingRule | None:
        row = self._rules().filter(department_id=department_id).first()
        return _to_rule(row) if row is not None else None

    def rule_for_update(self, department_id: UUID) -> RoutingRule | None:
        row = self._rules().select_for_update().filter(department_id=department_id).first()
        return _to_rule(row) if row is not None else None

    def intake_department(self) -> UUID | None:
        department_id: UUID | None = (
            self._rules()
            .filter(is_company_intake=True, enabled=True)
            .values_list("department_id", flat=True)
            .first()
        )
        return department_id

    def save_rule(self, rule: RoutingRule, *, updated_by: UUID) -> RoutingRule:
        if rule.is_company_intake:
            # One intake department per company: choosing a new one releases the old.
            self._rules().filter(is_company_intake=True).exclude(
                department_id=rule.department_id
            ).update(is_company_intake=False)
        row, _ = self._rules().update_or_create(
            department_id=rule.department_id,
            defaults={
                "enabled": rule.enabled,
                "member_ids": sorted(str(m) for m in rule.member_ids),
                "require_on_shift": rule.require_on_shift,
                "skip_on_absence": rule.skip_on_absence,
                "is_company_intake": rule.is_company_intake,
                "updated_by": updated_by,
            },
        )
        return _to_rule(row)

    def remember(self, department_id: UUID, user_id: UUID) -> None:
        self._rules().filter(department_id=department_id).update(last_assigned_user_id=user_id)

    def managers(self, department_id: UUID) -> list[UUID]:
        return list(
            Membership.objects.using("default")
            .filter(
                organization_id=self.tenant_id,
                department_id=department_id,
                role=Role.SALES_REP.value,
                is_active=True,
                organization__is_active=True,
                user__is_active=True,
            )
            .values_list("user_id", flat=True)
        )

    def on_shift(self, user_ids: list[UUID]) -> set[UUID]:
        if not user_ids:
            return set()
        return set(
            WorkSessionRecord.objects.using(self.using)
            .filter(user_id__in=user_ids, status=WorkSessionStatus.OPEN.value)
            .values_list("user_id", flat=True)
        )

    def absent_today(self, user_ids: list[UUID]) -> set[UUID]:
        if not user_ids:
            return set()
        timezone = (
            Organization.objects.using("default")
            .filter(id=self.tenant_id)
            .values_list("timezone", flat=True)
            .first()
        )
        today = datetime.now(ZoneInfo(timezone or _DEFAULT_TIMEZONE)).date()
        return set(
            AbsenceRecord.objects.using(self.using)
            .filter(user_id__in=user_ids, start_date__lte=today, end_date__gte=today)
            .values_list("user_id", flat=True)
        )

    def department_exists(self, department_id: UUID) -> bool:
        return (
            Department.objects.using("default")
            .filter(organization_id=self.tenant_id, id=department_id, is_active=True)
            .exists()
        )

    def notify_assignee_missing(self, department_id: UUID, lead: Lead) -> None:
        # Imported here: notifications composition imports back into the CRM modules.
        from modules.notifications.composition import notification_service

        heads = (
            Membership.objects.using("default")
            .filter(
                organization_id=self.tenant_id,
                department_id=department_id,
                role__in=_HEAD_ROLES,
                is_active=True,
                user__is_active=True,
            )
            .values_list("user_id", flat=True)
        )
        notifications = notification_service()
        for head in heads:
            notifications.notify(
                recipient_user_id=head,
                category=NotificationCategory.SYSTEM,
                title="Лид не распределён автоматически",
                body=(
                    f"«{lead.name}»: нет менеджеров на смене. "
                    "Лид остался у создателя — назначьте его вручную."  # noqa: RUF001
                ),
                source_event_id=lead.id,
                link=f"/prospects/{lead.id}",
            )
