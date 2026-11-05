from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from rest_framework import serializers

from modules.team_cabinet.application.service import MemberCard, TeamReport, TodayBoard
from modules.team_cabinet.domain.actions import (
    TITLES,
    ActionGroup,
    ActionItem,
    TodayActions,
    rule_text,
)
from modules.team_cabinet.domain.analytics import (
    FunnelReport,
    LeadResponse,
    LossReport,
    ReasonCount,
    ScoreHistory,
    StageStat,
)
from modules.team_cabinet.domain.board import DayScore, DepartmentTotals, ManagerDay
from modules.team_cabinet.domain.period import Discipline, PeriodMetrics
from modules.team_cabinet.domain.plan_fact import PlanFact, PlanFactRow


class ManagerDaySerializer(serializers.Serializer[dict[str, Any]]):
    user_id = serializers.UUIDField(read_only=True)
    username = serializers.CharField(read_only=True)
    department_id = serializers.UUIDField(read_only=True, allow_null=True)
    product_role = serializers.CharField(read_only=True)
    calls = serializers.IntegerField(read_only=True)
    meetings_total = serializers.IntegerField(read_only=True)
    meetings_confirmed = serializers.IntegerField(read_only=True)
    meetings_held = serializers.IntegerField(read_only=True)
    meetings_no_show = serializers.IntegerField(read_only=True)
    meetings_overdue_outcome = serializers.IntegerField(read_only=True)
    morning_meetings = serializers.IntegerField(read_only=True)
    morning_confirmed = serializers.IntegerField(read_only=True)
    payments_count = serializers.IntegerField(read_only=True)
    prepayments = serializers.IntegerField(read_only=True)
    received = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    open_tasks = serializers.IntegerField(read_only=True)
    overdue_tasks = serializers.IntegerField(read_only=True)
    meetings_to_confirm = serializers.IntegerField(read_only=True)
    next_to_confirm_at = serializers.DateTimeField(read_only=True, allow_null=True)
    meetings_cancelled = serializers.IntegerField(read_only=True)
    reschedules_without_date = serializers.IntegerField(read_only=True)
    tasks_due_today = serializers.IntegerField(read_only=True)
    tasks_done_today = serializers.IntegerField(read_only=True)
    plan_sales = serializers.DecimalField(
        max_digits=16, decimal_places=2, read_only=True, allow_null=True
    )
    sales_month = serializers.IntegerField(read_only=True)
    score = serializers.DictField(read_only=True, allow_null=True)
    has_activity = serializers.BooleanField(read_only=True)
    on_absence = serializers.BooleanField(read_only=True)
    status = serializers.CharField(read_only=True)
    red_reasons = serializers.ListField(child=serializers.CharField(), read_only=True)
    yellow_reasons = serializers.ListField(child=serializers.CharField(), read_only=True)


class DepartmentTotalsSerializer(serializers.Serializer[dict[str, Any]]):
    managers = serializers.IntegerField(read_only=True)
    calls = serializers.IntegerField(read_only=True)
    meetings_total = serializers.IntegerField(read_only=True)
    meetings_held = serializers.IntegerField(read_only=True)
    payments_count = serializers.IntegerField(read_only=True)
    prepayments = serializers.IntegerField(read_only=True)
    received = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    red = serializers.IntegerField(read_only=True)
    yellow = serializers.IntegerField(read_only=True)
    green = serializers.IntegerField(read_only=True)


class TodayBoardSerializer(serializers.Serializer[dict[str, Any]]):
    as_of = serializers.DateTimeField(read_only=True)
    date = serializers.DateField(read_only=True)
    managers = ManagerDaySerializer(many=True, read_only=True)
    totals = DepartmentTotalsSerializer(read_only=True)


def manager_day_to_dict(day: ManagerDay) -> dict[str, Any]:
    return {
        "user_id": str(day.user_id),
        "username": day.username,
        "department_id": str(day.department_id) if day.department_id else None,
        "product_role": day.product_role,
        "calls": day.calls,
        "meetings_total": day.meetings_total,
        "meetings_confirmed": day.meetings_confirmed,
        "meetings_held": day.meetings_held,
        "meetings_no_show": day.meetings_no_show,
        "meetings_overdue_outcome": day.meetings_overdue_outcome,
        "morning_meetings": day.morning_meetings,
        "morning_confirmed": day.morning_confirmed,
        "payments_count": day.payments_count,
        "prepayments": day.prepayments,
        "received": str(day.received),
        "open_tasks": day.open_tasks,
        "overdue_tasks": day.overdue_tasks,
        "meetings_to_confirm": day.meetings_to_confirm,
        "next_to_confirm_at": day.next_to_confirm_at.isoformat()
        if day.next_to_confirm_at
        else None,
        "meetings_cancelled": day.meetings_cancelled,
        "reschedules_without_date": day.reschedules_without_date,
        "tasks_due_today": day.tasks_due_today,
        "tasks_done_today": day.tasks_done_today,
        "plan_sales": str(day.plan_sales) if day.plan_sales is not None else None,
        "sales_month": day.sales_month,
        "score": score_to_dict(day.score) if day.score else None,
        "has_activity": day.has_activity,
        "on_absence": day.on_absence,
        "status": day.status,
        "red_reasons": list(day.red_reasons),
        "yellow_reasons": list(day.yellow_reasons),
    }


def score_to_dict(score: DayScore) -> dict[str, Any]:
    return {
        "points": score.points,
        "grade": score.grade,
        "result": str(score.result) if score.result is not None else None,
        "discipline": str(score.discipline),
        "plan_missing": score.plan_missing,
    }


def totals_to_dict(totals: DepartmentTotals) -> dict[str, Any]:
    return {
        "meetings_confirmed": totals.meetings_confirmed,
        "meetings_to_confirm": totals.meetings_to_confirm,
        "meetings_no_show": totals.meetings_no_show,
        "meetings_cancelled": totals.meetings_cancelled,
        "meetings_overdue_outcome": totals.meetings_overdue_outcome,
        "reschedules_without_date": totals.reschedules_without_date,
        "open_tasks": totals.open_tasks,
        "overdue_tasks": totals.overdue_tasks,
        "tasks_due_today": totals.tasks_due_today,
        "managers": totals.managers,
        "calls": totals.calls,
        "meetings_total": totals.meetings_total,
        "meetings_held": totals.meetings_held,
        "payments_count": totals.payments_count,
        "prepayments": totals.prepayments,
        "received": str(totals.received),
        "red": totals.red,
        "yellow": totals.yellow,
        "green": totals.green,
    }


class PlanFactRowSerializer(serializers.Serializer[dict[str, Any]]):
    user_id = serializers.UUIDField(read_only=True)
    username = serializers.CharField(read_only=True)
    department_id = serializers.UUIDField(read_only=True, allow_null=True)
    product_role = serializers.CharField(read_only=True)
    plan_sales = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    sales_actual = serializers.IntegerField(read_only=True)
    received_actual = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    remaining_sales = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    forecast_sales = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    working_days_total = serializers.IntegerField(read_only=True)
    working_days_elapsed = serializers.IntegerField(read_only=True)


class PlanFactSerializer(serializers.Serializer[dict[str, Any]]):
    month = serializers.CharField(read_only=True)
    working_days_total = serializers.IntegerField(read_only=True)
    working_days_elapsed = serializers.IntegerField(read_only=True)
    plan_sales_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    sales_actual_total = serializers.IntegerField(read_only=True)
    received_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    forecast_sales_total = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    rows = PlanFactRowSerializer(many=True, read_only=True)


def plan_fact_row_to_dict(row: PlanFactRow) -> dict[str, Any]:
    return {
        "user_id": str(row.user_id),
        "username": row.username,
        "department_id": str(row.department_id) if row.department_id else None,
        "product_role": row.product_role,
        "plan_sales": str(row.plan_sales),
        "sales_actual": row.sales_actual,
        "received_actual": str(row.received_actual),
        "remaining_sales": str(row.remaining_sales),
        "forecast_sales": str(row.forecast_sales),
        "working_days_total": row.working_days_total,
        "working_days_elapsed": row.working_days_elapsed,
    }


def plan_fact_to_dict(plan_fact: PlanFact) -> dict[str, Any]:
    return {
        "month": plan_fact.month,
        "working_days_total": plan_fact.working_days_total,
        "working_days_elapsed": plan_fact.working_days_elapsed,
        "plan_sales_total": str(plan_fact.plan_sales_total),
        "sales_actual_total": plan_fact.sales_actual_total,
        "received_total": str(plan_fact.received_total),
        "forecast_sales_total": str(plan_fact.forecast_sales_total),
        "working_days_left": plan_fact.working_days_left,
        "department_id": str(plan_fact.department_id) if plan_fact.department_id else None,
        "department_plan_sales": _dec(plan_fact.department_plan_sales),
        "department_remaining": _dec(plan_fact.department_remaining),
        "department_per_day_needed": _dec(plan_fact.department_per_day_needed),
        "department_percent": _dec(plan_fact.department_percent),
        "rows": [plan_fact_row_to_dict(r) for r in plan_fact.rows],
    }


def _dec(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _ratio(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def period_to_dict(m: PeriodMetrics) -> dict[str, Any]:
    return {
        "meetings_booked": m.meetings_booked,
        "meetings_held": m.meetings_held,
        "meetings_no_show": m.meetings_no_show,
        "meetings_confirmed": m.meetings_confirmed,
        "meetings_cancelled": m.meetings_cancelled,
        "calls": m.calls,
        "stage_changes": m.stage_changes,
        "payments_count": m.payments_count,
        "received": str(m.received),
        "prepayments": m.prepayments,
        "sales": m.sales,
        "tasks_done": m.tasks_done,
        "attendance": _ratio(m.attendance),
        "meeting_to_sale": _ratio(m.meeting_to_sale),
    }


def discipline_to_dict(d: Discipline) -> dict[str, Any]:
    return {
        "meetings_without_outcome": d.meetings_without_outcome,
        "reschedules_without_date": d.reschedules_without_date,
        "deals_without_next_step": d.deals_without_next_step,
        "overdue_tasks": d.overdue_tasks,
    }


def member_card_to_dict(card: MemberCard) -> dict[str, Any]:
    days = [card.start + timedelta(days=i) for i in range((card.end - card.start).days + 1)]
    return {
        "user_id": str(card.member.user_id),
        "username": card.member.username,
        "department_id": str(card.member.department_id) if card.member.department_id else None,
        "date_from": card.start.isoformat(),
        "date_to": card.end.isoformat(),
        "current": period_to_dict(card.current),
        "previous": period_to_dict(card.previous),
        "department_average": {k: str(v) for k, v in card.department_average.items()},
        "discipline": discipline_to_dict(card.discipline),
        "month": card.month,
        "plan_sales": _dec(card.plan_sales),
        "month_sales": card.month_sales,
        "activity": [
            {
                "date": day.isoformat(),
                "calls": card.current.daily.get(day, {}).get("calls", 0),
                "meetings": card.current.daily.get(day, {}).get("meetings", 0),
                "stage_changes": card.current.daily.get(day, {}).get("stage_changes", 0),
                "payments": card.current.daily.get(day, {}).get("payments", 0),
                "absent": day in card.absent_days,
            }
            for day in days
        ],
        "days_without_activity": [d.isoformat() for d in card.days_without_activity],
    }


REPORT_COLUMNS = (
    "username",
    "meetings_booked",
    "meetings_held",
    "meetings_no_show",
    "meetings_confirmed",
    "calls",
    "payments_count",
    "received",
    "prepayments",
    "sales",
    "tasks_done",
    "overdue_tasks",
    "meetings_without_outcome",
    "deals_without_next_step",
    "plan_sales",
    "month_sales",
)


def team_report_to_dict(report: TeamReport) -> dict[str, Any]:
    rows = []
    totals = PeriodMetrics()
    for row in report.rows:
        m = row.metrics
        for key in (
            "meetings_booked",
            "meetings_held",
            "meetings_no_show",
            "meetings_confirmed",
            "meetings_cancelled",
            "calls",
            "stage_changes",
            "payments_count",
            "prepayments",
            "sales",
            "tasks_done",
        ):
            setattr(totals, key, getattr(totals, key) + getattr(m, key))
        totals.received += m.received
        rows.append(
            {
                "user_id": str(row.member.user_id),
                "username": row.member.username,
                "department_id": (
                    str(row.member.department_id) if row.member.department_id else None
                ),
                "product_role": row.member.product_role,
                **period_to_dict(m),
                **discipline_to_dict(row.discipline),
                "plan_sales": _dec(row.plan_sales),
                "month_sales": row.month_sales,
            }
        )
    return {
        "date_from": report.start.isoformat(),
        "date_to": report.end.isoformat(),
        "department_id": str(report.department_id) if report.department_id else None,
        "rows": rows,
        "totals": period_to_dict(totals),
    }


def board_to_dict(board: TodayBoard) -> dict[str, Any]:
    return {
        "as_of": board.as_of.isoformat(),
        "date": board.day.isoformat() if board.day else None,
        "managers": [manager_day_to_dict(m) for m in board.managers],
        "totals": totals_to_dict(board.totals),
    }


def stage_to_dict(stage: StageStat) -> dict[str, Any]:
    return {
        "stage_id": stage.stage_id,
        "name": stage.name,
        "kind": stage.kind,
        "position": stage.position,
        "open_deals": stage.open_deals,
        "open_amount": _dec(stage.open_amount),
        "median_age_days": _dec(stage.median_age_days),
        "stale_deals": stage.stale_deals,
        "reached": stage.reached,
        "reached_previous": stage.reached_previous,
        "moved_on": stage.moved_on,
        "conversion": _ratio(stage.conversion),
        "is_bottleneck": stage.is_bottleneck,
    }


def funnel_to_dict(report: FunnelReport) -> dict[str, Any]:
    return {
        "pipeline_id": report.pipeline_id,
        "pipeline_name": report.pipeline_name,
        "date_from": report.start,
        "date_to": report.end,
        "previous_from": report.previous_start,
        "previous_to": report.previous_end,
        "stale_days": report.stale_days,
        "stages": [stage_to_dict(s) for s in report.stages],
        "won": report.won,
        "lost": report.lost,
        "won_previous": report.won_previous,
        "lost_previous": report.lost_previous,
    }


def reason_to_dict(reason: ReasonCount) -> dict[str, Any]:
    return {"reason": reason.reason, "total": reason.total, "by_month": dict(reason.by_month)}


def lead_response_to_dict(lead: LeadResponse) -> dict[str, Any]:
    return {
        "leads": lead.leads,
        "responded": lead.responded,
        "median_minutes": _dec(lead.median_minutes),
        "within_15_min": lead.within_15_min,
        "within_60_min": lead.within_60_min,
    }


def losses_to_dict(report: LossReport) -> dict[str, Any]:
    return {
        "date_from": report.start,
        "date_to": report.end,
        "previous_from": report.previous_start,
        "previous_to": report.previous_end,
        "months": report.months,
        "lost_deals": report.lost_deals,
        "lost_previous": report.lost_previous,
        "loss_reasons": [reason_to_dict(r) for r in report.loss_reasons],
        "meetings_total": report.meetings_total,
        "meetings_cancelled": report.meetings_cancelled,
        "meetings_no_show": report.meetings_no_show,
        "cancelled_previous": report.cancelled_previous,
        "no_show_previous": report.no_show_previous,
        "cancel_reasons": [reason_to_dict(r) for r in report.cancel_reasons],
        "no_show_reasons": [reason_to_dict(r) for r in report.no_show_reasons],
        "lead_response": lead_response_to_dict(report.lead_response),
    }


def score_history_to_dict(history: ScoreHistory) -> dict[str, Any]:
    return {
        "user_id": history.user_id,
        "date_from": history.start,
        "date_to": history.end,
        "generated_at": history.generated_at,
        "points": [
            {"day": p.day, "points": p.points, "grade": p.grade, "reason": p.reason}
            for p in history.points
        ],
    }


def action_item_to_dict(item: ActionItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "title": item.title,
        "manager_id": str(item.manager_id) if item.manager_id else None,
        "manager_username": item.manager_username,
        "amount": _dec(item.amount),
        "currency": item.currency,
        "date": item.date.isoformat() if item.date else None,
        "at": item.at.isoformat() if item.at else None,
        "age_days": item.age_days,
        "subject_type": item.subject_type,
        "subject_id": str(item.subject_id) if item.subject_id else None,
        "reasons": list(item.reasons),
        "paid": _dec(item.paid),
        "debt": _dec(item.debt),
        "stage_name": item.stage_name,
    }


def action_group_to_dict(
    group: ActionGroup, priority: int, actions: TodayActions
) -> dict[str, Any]:
    return {
        "kind": group.kind.value,
        "priority": priority,
        "title": TITLES[group.kind],
        "rule": rule_text(group.kind, actions.thresholds),
        "count": group.count,
        "amounts": [
            {"currency": currency, "total": str(total)}
            for currency, total in sorted(group.amounts.items())
        ],
        "by_manager": [
            {"user_id": str(uid), "username": actions.usernames.get(uid, ""), "count": count}
            for uid, count in sorted(group.by_manager.items(), key=lambda kv: (-kv[1], str(kv[0])))
        ],
        "items": [action_item_to_dict(item) for item in group.items],
    }


def today_actions_to_dict(actions: TodayActions) -> dict[str, Any]:
    return {
        "date": actions.day.isoformat(),
        "as_of": actions.as_of.isoformat(),
        "limit": actions.limit,
        "department_id": str(actions.department_id) if actions.department_id else None,
        "thresholds": {
            "stalled_deal_days": actions.thresholds.stalled_deal_days,
            "lead_response_minutes": actions.thresholds.lead_response_minutes,
        },
        "total": actions.total,
        "groups": [
            action_group_to_dict(group, index + 1, actions)
            for index, group in enumerate(actions.groups)
        ],
    }
