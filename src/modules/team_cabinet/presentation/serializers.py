from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.team_cabinet.application.service import TodayBoard
from modules.team_cabinet.domain.board import DepartmentTotals, ManagerDay


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
    received = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    open_tasks = serializers.IntegerField(read_only=True)
    overdue_tasks = serializers.IntegerField(read_only=True)
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
    received = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    red = serializers.IntegerField(read_only=True)
    yellow = serializers.IntegerField(read_only=True)
    green = serializers.IntegerField(read_only=True)


class TodayBoardSerializer(serializers.Serializer[dict[str, Any]]):
    as_of = serializers.DateTimeField(read_only=True)
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
        "received": str(day.received),
        "open_tasks": day.open_tasks,
        "overdue_tasks": day.overdue_tasks,
        "has_activity": day.has_activity,
        "on_absence": day.on_absence,
        "status": day.status,
        "red_reasons": list(day.red_reasons),
        "yellow_reasons": list(day.yellow_reasons),
    }


def totals_to_dict(totals: DepartmentTotals) -> dict[str, Any]:
    return {
        "managers": totals.managers,
        "calls": totals.calls,
        "meetings_total": totals.meetings_total,
        "meetings_held": totals.meetings_held,
        "payments_count": totals.payments_count,
        "received": str(totals.received),
        "red": totals.red,
        "yellow": totals.yellow,
        "green": totals.green,
    }


def board_to_dict(board: TodayBoard) -> dict[str, Any]:
    return {
        "as_of": board.as_of.isoformat(),
        "managers": [manager_day_to_dict(m) for m in board.managers],
        "totals": totals_to_dict(board.totals),
    }
