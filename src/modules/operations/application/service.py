"""Operations use cases: findings for the caller and per-company thresholds."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Scope
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.operations.application.ports import OperationsReader, SettingsStore, Visibility
from modules.operations.domain.rules import Finding, Thresholds, counts, detect
from shared.application.ports import UnitOfWork

MAX_FINDINGS = 500


@dataclass(frozen=True, slots=True)
class OperationsReport:
    generated_at: datetime
    thresholds: Thresholds
    counts: dict[str, int]
    findings: list[Finding]  # oldest first, at most MAX_FINDINGS
    truncated: bool
    # Categories left out because the caller may not view that kind of record.
    skipped: tuple[str, ...]


class OperationsService:
    def __init__(
        self,
        reader: OperationsReader,
        settings: SettingsStore,
        uow: UnitOfWork,
        audit: AuditLog,
        *,
        policy: AccessPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._reader = reader
        self._settings = settings
        self._uow = uow
        self._audit = audit
        self._policy = policy or AccessPolicy()
        self._clock = clock

    def report(self, principal: Principal) -> OperationsReport:
        """Findings over the records the principal can see, under company thresholds."""
        now = self._clock()
        today = now.date()
        thresholds = self._settings.load()
        skipped: list[str] = []

        lead_vis = self._visibility(principal, Permission.LEADS_VIEW)
        deal_vis = self._visibility(principal, Permission.DEALS_VIEW)
        task_vis = self._visibility(principal, Permission.ACTIVITIES_VIEW)
        leads = (
            self._reader.stale_lead_candidates(
                lead_vis, now - timedelta(days=thresholds.stale_lead_days)
            )
            if lead_vis is not None
            else []
        )
        deals = self._reader.open_deals(deal_vis, today) if deal_vis is not None else []
        tasks = self._reader.overdue_tasks(task_vis, today) if task_vis is not None else []
        for name, vis in (("leads", lead_vis), ("deals", deal_vis), ("tasks", task_vis)):
            if vis is None:
                skipped.append(name)

        findings = detect(now=now, thresholds=thresholds, leads=leads, deals=deals, tasks=tasks)
        return OperationsReport(
            generated_at=now,
            thresholds=thresholds,
            counts=counts(findings),
            findings=findings[:MAX_FINDINGS],
            truncated=len(findings) > MAX_FINDINGS,
            skipped=tuple(skipped),
        )

    def thresholds(self, principal: Principal) -> Thresholds:
        # Anyone who can see any operational data may see the rules applied to it.
        if not any(
            self._policy.allows(principal, p)
            for p in (Permission.LEADS_VIEW, Permission.DEALS_VIEW, Permission.ACTIVITIES_VIEW)
        ):
            self._policy.require(principal, Permission.OPERATIONS_MANAGE)
        return self._settings.load()

    def update_thresholds(self, principal: Principal, thresholds: Thresholds) -> Thresholds:
        self._policy.require(principal, Permission.OPERATIONS_MANAGE)
        with self._uow:
            self._settings.save(thresholds, updated_by=principal.user_id)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="operations_settings.updated",
                    resource_type="operations_settings",
                    resource_id="thresholds",
                    changed_fields=("stale_lead_days", "stalled_deal_days"),
                )
            )
        return thresholds

    def _visibility(self, principal: Principal, permission: Permission) -> Visibility | None:
        scope = self._policy.scope_for(principal, permission)
        if scope is None:
            return None
        if scope is Scope.ALL:
            return Visibility()
        if scope is Scope.TEAM:
            return Visibility(owner_user_id=principal.user_id, team_ids=principal.team_ids)
        return Visibility(owner_user_id=principal.user_id)
