"""Risk use cases: signals for review and per-company thresholds.

Signals concern what employees did, so only roles with ``risk.view`` (owner,
admin) see them, always company-wide. A signal is a reason to check, never an
automatic conclusion about a person.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from modules.risk.application.ports import WATCHED_ACTIONS, RiskReader, SettingsStore
from modules.risk.domain.rules import Signal, Thresholds, counts, detect, window_start
from shared.application.ports import UnitOfWork

MAX_SIGNALS = 500


@dataclass(frozen=True, slots=True)
class RiskReport:
    generated_at: datetime
    thresholds: Thresholds
    counts: dict[str, int]
    signals: list[Signal]  # newest first, at most MAX_SIGNALS
    truncated: bool


class RiskService:
    def __init__(
        self,
        reader: RiskReader,
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

    def report(self, principal: Principal) -> RiskReport:
        self._policy.require(principal, Permission.RISK_VIEW)
        now = self._clock()
        thresholds = self._settings.load()
        since = window_start(now, thresholds)
        signals = detect(
            today=now.date(),
            thresholds=thresholds,
            won=self._reader.won_deals(since),
            open_deals=self._reader.open_deals(),
            events=self._reader.audit_events(since, WATCHED_ACTIONS),
        )
        return RiskReport(
            generated_at=now,
            thresholds=thresholds,
            counts=counts(signals),
            signals=signals[:MAX_SIGNALS],
            truncated=len(signals) > MAX_SIGNALS,
        )

    def thresholds(self, principal: Principal) -> Thresholds:
        self._policy.require(principal, Permission.RISK_VIEW)
        return self._settings.load()

    def update_thresholds(self, principal: Principal, thresholds: Thresholds) -> Thresholds:
        self._policy.require(principal, Permission.RISK_MANAGE)
        with self._uow:
            self._settings.save(thresholds, updated_by=principal.user_id)
            self._audit.record(
                AuditEntry(
                    actor_user_id=principal.user_id,
                    action="risk_settings.updated",
                    resource_type="risk_settings",
                    resource_id="thresholds",
                    changed_fields=tuple(asdict(thresholds)),
                )
            )
        return thresholds
