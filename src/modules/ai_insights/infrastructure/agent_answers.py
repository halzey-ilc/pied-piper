"""Answers to data questions, read through the owning modules' services as the user,
so each module's visibility rules decide what is counted."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from modules.access_control.domain.rbac import Principal
from modules.activities.application.ports import ActivityFilter
from modules.activities.composition import activity_service
from modules.activities.domain.activity import ActivityKind
from modules.ai_insights.domain.agent_questions import (
    AnswerItem,
    DataAnswer,
    Question,
    answer_text,
)
from modules.leads.composition import lead_service
from modules.organizations.composition import member_display_name
from modules.planning.application.ports import PlanFilter
from modules.planning.application.service import PlanProgress
from modules.planning.composition import planning_service
from modules.planning.domain.plan import PlanMetric, PlanScope, period_granularity
from modules.sales_pipeline.application.ports import DealFilter
from modules.sales_pipeline.composition import deal_service
from modules.sales_pipeline.domain.deal import DealStatus
from platform_core.application.context import require_tenant

_SHOWN = 5  # records listed under a count
_PLANS = 20

_METRICS: dict[str, dict[PlanMetric, str]] = {
    "ru": {
        PlanMetric.MONTHLY_SALES: "продажи",
        PlanMetric.MONTHLY_TURNOVER: "оборот",
        PlanMetric.WEEKLY_LEADS: "лиды",
        PlanMetric.WEEKLY_PREPAYMENTS: "предоплаты",
        PlanMetric.DAILY_CALLS: "звонки",
    },
    "ky": {
        PlanMetric.MONTHLY_SALES: "сатуулар",
        PlanMetric.MONTHLY_TURNOVER: "жүгүртүү",
        PlanMetric.WEEKLY_LEADS: "лиддер",
        PlanMetric.WEEKLY_PREPAYMENTS: "алдын ала төлөмдөр",
        PlanMetric.DAILY_CALLS: "чалуулар",
    },
    "en": {
        PlanMetric.MONTHLY_SALES: "sales",
        PlanMetric.MONTHLY_TURNOVER: "turnover",
        PlanMetric.WEEKLY_LEADS: "leads",
        PlanMetric.WEEKLY_PREPAYMENTS: "prepayments",
        PlanMetric.DAILY_CALLS: "calls",
    },
}
_WHOLE = {"ru": "Компания", "ky": "Компания", "en": "Company"}
_DEPARTMENT = {"ru": "Отдел", "ky": "Бөлүм", "en": "Department"}
_OF = {"ru": "из", "ky": "/", "en": "of"}


def plan_period(metric: PlanMetric, today: date) -> str:
    granularity = period_granularity(metric)
    if granularity == "month":
        return f"{today:%Y-%m}"
    if granularity == "week":
        year, week, _ = today.isocalendar()
        return f"{year}-W{week:02d}"
    return today.isoformat()


def _number(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _owner(progress: PlanProgress, language: str) -> str:
    plan = progress.plan
    if plan.scope is PlanScope.COMPANY:
        return _WHOLE[language]
    if plan.scope is PlanScope.DEPARTMENT or plan.scope_id is None:
        return _DEPARTMENT[language]
    return member_display_name(require_tenant().tenant_id, plan.scope_id) or "—"


def _plan_line(progress: PlanProgress, language: str) -> str:
    plan = progress.plan
    target = _number(plan.target)
    done = "—" if progress.actual is None else _number(progress.actual)
    percent = "" if progress.percent is None else f" ({_number(progress.percent)}%)"
    metric = _METRICS[language][plan.metric]
    return f"{_owner(progress, language)}: {metric} {done} {_OF[language]} {target}{percent}"


class CrmDataAnswers:
    def answer(
        self, principal: Principal, question: Question, *, today: date, language: str
    ) -> DataAnswer:
        if question is Question.LEADS_COUNT:
            total = lead_service().list_leads(principal, limit=1, offset=0).total
            return DataAnswer(answer_text(language, question, total))
        if question is Question.DEALS_COUNT:
            page = deal_service().list_deals(
                principal, DealFilter(status=DealStatus.OPEN), limit=1, offset=0
            )
            return DataAnswer(answer_text(language, question, page.total))
        if question is Question.OVERDUE_DEALS:
            page = deal_service().list_deals(
                principal,
                DealFilter(status=DealStatus.OPEN, attention="overdue", today=today),
                limit=_SHOWN,
                offset=0,
            )
            items = tuple(AnswerItem(d.title, f"deal:{d.id}") for d in page.items)
            return DataAnswer(answer_text(language, question, page.total), items)
        if question is Question.OVERDUE_TASKS:
            tasks = activity_service().timeline(
                principal,
                ActivityFilter(kind=ActivityKind.TASK, overdue_before=today),
                limit=_SHOWN,
                offset=0,
            )
            items = tuple(AnswerItem(t.title or "—") for t in tasks.items)
            return DataAnswer(answer_text(language, question, tasks.total), items)
        return self._plans(principal, today, language)

    def _plans(self, principal: Principal, today: date, language: str) -> DataAnswer:
        lines: list[str] = []
        for metric in PlanMetric:
            page = planning_service().progress(
                principal,
                PlanFilter(metric=metric, period=plan_period(metric, today)),
                limit=_PLANS,
                offset=0,
            )
            lines += [_plan_line(p, language) for p in page.items]
        if not lines:
            return DataAnswer(answer_text(language, "no_plans"))
        summary = answer_text(language, Question.PLAN_PROGRESS, len(lines))
        return DataAnswer(summary, tuple(AnswerItem(line) for line in lines))
