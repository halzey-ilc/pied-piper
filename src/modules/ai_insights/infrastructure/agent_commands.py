"""Value-changing commands checked against the data, read as the user. Nothing changes
here: the owning modules run the action after the user confirms (agent_tools)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.domain.agent import Subject, Tool
from modules.ai_insights.domain.agent_commands import (
    Command,
    CommandKind,
    CommandRefusedError,
    Prepared,
    change,
)
from modules.ai_insights.infrastructure.agent_answers import plan_period
from modules.meetings.application.ports import MeetingFilter
from modules.meetings.composition import meeting_service
from modules.meetings.domain.meeting import MeetingStatus
from modules.meetings.domain.meeting import SubjectType as MeetingSubject
from modules.organizations.composition import list_active_members, member_display_name
from modules.planning.application.ports import PlanFilter
from modules.planning.composition import planning_service
from modules.planning.domain.plan import Plan, PlanMetric, PlanScope, period_granularity
from modules.sales_pipeline.composition import deal_service
from platform_core.application.context import require_tenant

_PLANS = 20
_MIN_STEM = 4  # shorter parts of a name match too much

_OUTCOME_LABELS = {"held": "Состоялась", "no_show": "Клиент не пришёл", "cancelled": "Отменена"}


def _money(value: Decimal) -> str:
    return f"{value.normalize():,f}".replace(",", " ")


def _named(request: str, name: str) -> bool:
    """Whether the request mentions this person, in any case ending («Айгериму»)."""
    words = request.casefold().split()
    for part in name.casefold().split():
        stem = part[: max(_MIN_STEM, len(part) - 1)]
        if len(part) >= _MIN_STEM and any(w.startswith(stem) for w in words):
            return True
    return False


def _period(command: Command, metric: PlanMetric, today: date) -> str:
    if command.month is None or period_granularity(metric) != "month":
        return plan_period(metric, today)
    year = today.year + (1 if command.month < today.month else 0)
    return f"{year}-{command.month:02d}"


def _pick_plan(plans: list[Plan], request: str) -> Plan:
    if not plans:
        raise CommandRefusedError("no_plan")
    if len(plans) == 1:
        return plans[0]
    company = [p for p in plans if p.scope is PlanScope.COMPANY]
    if company and "компан" in request.casefold():
        return company[0]
    raise CommandRefusedError("several_plans")


class CrmCommands:
    def prepare(
        self, principal: Principal, command: Command, subject: Subject | None, *, today: date
    ) -> Prepared:
        if command.kind is CommandKind.PLAN_TARGET:
            return self._plan(principal, command, today)
        if subject is None or subject.kind != "deal":
            raise CommandRefusedError("need_deal")
        if command.kind is CommandKind.DEAL_AMOUNT:
            return self._amount(principal, command, subject)
        if command.kind is CommandKind.DEAL_OWNER:
            return self._owner(principal, command, subject)
        return self._meeting(principal, command, subject)

    def _amount(self, principal: Principal, command: Command, subject: Subject) -> Prepared:
        deal = deal_service().get(principal, subject.id)
        amount = command.number or Decimal(0)
        currency = deal.value.currency
        return Prepared(
            Tool.UPDATE_DEAL_AMOUNT,
            subject,
            {
                "amount": str(amount),
                "change": change(
                    "Сумма",
                    f"{_money(deal.value.amount)} {currency}",
                    f"{_money(amount)} {currency}",
                ),
            },
        )

    def _owner(self, principal: Principal, command: Command, subject: Subject) -> Prepared:
        tenant_id = require_tenant().tenant_id
        deal = deal_service().get(principal, subject.id)
        request = command.request.replace(subject.title, " ")
        people = [
            (user_id, name)
            for user_id, _ in list_active_members(tenant_id)
            if (name := member_display_name(tenant_id, user_id)) and _named(request, name)
        ]
        if not people:
            raise CommandRefusedError("no_member")
        if len(people) > 1:
            raise CommandRefusedError("several_members")
        user_id, name = people[0]
        before = member_display_name(tenant_id, deal.owner_user_id) or "—"
        return Prepared(
            Tool.CHANGE_DEAL_OWNER,
            subject,
            {"owner_user_id": str(user_id), "change": change("Ответственный", before, name)},
        )

    def _plan(self, principal: Principal, command: Command, today: date) -> Prepared:
        metric = PlanMetric(str(command.metric))
        period = _period(command, metric, today)
        plans = (
            planning_service()
            .list_plans(principal, PlanFilter(metric=metric, period=period), limit=_PLANS, offset=0)
            .items
        )
        plan = _pick_plan(plans, command.request)
        target = command.number or Decimal(0)
        subject = Subject(ref=f"plan:{plan.id}", kind="plan", title=f"{metric.value} {period}")
        return Prepared(
            Tool.UPDATE_PLAN,
            subject,
            {
                "scope": plan.scope.value,
                "scope_id": str(plan.scope_id) if plan.scope_id else None,
                "metric": metric.value,
                "period": period,
                "target": str(target),
                "change": change(f"План, {period}", _money(plan.target), _money(target)),
            },
        )

    def _meeting(self, principal: Principal, command: Command, subject: Subject) -> Prepared:
        meetings = (
            meeting_service()
            .list_meetings(
                principal,
                MeetingFilter(
                    status=MeetingStatus.SCHEDULED,
                    subject_type=MeetingSubject.DEAL,
                    subject_id=subject.id,
                ),
                limit=2,
                offset=0,
            )
            .items
        )
        if not meetings:
            raise CommandRefusedError("no_meeting")
        if len(meetings) > 1:
            raise CommandRefusedError("several_meetings")
        meeting = meetings[0]
        outcome = command.outcome or "held"
        return Prepared(
            Tool.RECORD_MEETING_OUTCOME,
            Subject(ref=f"meeting:{meeting.id}", kind="meeting", title=meeting.title),
            {
                "outcome": outcome,
                "comment": command.comment,
                "change": change(
                    f"Встреча «{meeting.title}»", "Назначена", _OUTCOME_LABELS[outcome]
                ),
            },
        )
