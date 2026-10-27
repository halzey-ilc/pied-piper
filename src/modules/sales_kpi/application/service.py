"""Analytics read service. KPIs are tenant-wide; access needs reports.view."""

from __future__ import annotations

from datetime import date

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.sales_kpi.application.calculator import KpiCalculator, KpiValue
from modules.sales_kpi.application.ports import KpiSnapshotStore
from modules.sales_kpi.domain.kpi import KpiSnapshot, Period


class AnalyticsService:
    def __init__(
        self,
        calculator: KpiCalculator,
        snapshots: KpiSnapshotStore,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._calculator = calculator
        self._snapshots = snapshots
        self._policy = policy or AccessPolicy()

    def kpis(self, principal: Principal, period: Period, period_start: date) -> list[KpiValue]:
        self._policy.require(principal, Permission.REPORTS_VIEW)
        return self._calculator.compute(period, period_start)

    def history(
        self, principal: Principal, period: Period, start: date, end: date
    ) -> list[KpiSnapshot]:
        self._policy.require(principal, Permission.REPORTS_VIEW)
        return self._snapshots.list_between(period, start, end)
