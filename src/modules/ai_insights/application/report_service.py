"""AI reports: explanations of numbers the CRM computed (AI Operations, AI Business Analyst).

The CRM decides what the numbers are; the AI Platform only explains them. A
user gets reports about the data they can see, on demand or (digests) daily.
Reports are personal: nobody can read another user's report.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal, Role
from modules.ai_insights.application.ports import AiPlatformClient
from modules.ai_insights.application.report_ports import (
    FindingsSnapshot,
    FindingsSource,
    KpiSource,
    MemberDirectory,
    ReportRepository,
    ReportScheduler,
)
from modules.ai_insights.application.service import AiDisabledError, poll_delay
from modules.ai_insights.domain.insight import InsightStatus
from modules.ai_insights.domain.kpi_payload import (
    PERIODS,
    build_payload,
    period_start,
    previous_start,
)
from modules.ai_insights.domain.report import (
    MAX_FINDINGS_SENT,
    AiReport,
    ReportKind,
    ReportTrigger,
    validate_language,
)
from modules.audit.application.ports import AuditLog
from modules.audit.domain.entry import AuditEntry
from platform_core.application.ports import EventOutbox
from shared.application.ports import UnitOfWork
from shared.application.retry import PermanentError
from shared.domain.errors import DomainValidationError, NotFoundError
from shared.domain.events import DomainEvent

logger = logging.getLogger(__name__)

AGGREGATE = "ai_report"


class AiReportService:
    def __init__(
        self,
        repository: ReportRepository,
        findings: FindingsSource,
        kpis: KpiSource,
        members: MemberDirectory,
        client: AiPlatformClient,
        scheduler: ReportScheduler,
        uow: UnitOfWork,
        outbox: EventOutbox,
        audit: AuditLog,
        *,
        tenant_slug: str,
        tenant_id: UUID,
        enabled: bool,
        default_language: str = "ru",
        deadline_seconds: float = 900.0,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._findings = findings
        self._kpis = kpis
        self._members = members
        self._client = client
        self._scheduler = scheduler
        self._uow = uow
        self._outbox = outbox
        self._audit = audit
        self._tenant_slug = tenant_slug
        self._tenant_id = tenant_id
        self._enabled = enabled
        self._language = default_language
        self._deadline = deadline_seconds
        self._policy = policy or AccessPolicy()

    # --- operations digest ----------------------------------------------------

    def request_digest(
        self, principal: Principal, *, language: str | None = None
    ) -> tuple[AiReport, bool]:
        """Explain the principal's current operations findings. Returns (report, created)."""
        self._require_ai(principal)
        return self._create(
            principal,
            ReportKind.OPERATIONS_DIGEST,
            ReportTrigger.MANUAL,
            self._digest_payload(self._findings.snapshot_for(principal), language),
        )

    def create_daily_digests(self) -> int:
        """One digest per member allowed to use AI who has findings today. Idempotent."""
        if not self._enabled:
            return 0
        today = datetime.now(UTC).date()
        created = 0
        for member in self._members.active_members(self._tenant_id):
            try:
                principal = Principal(
                    user_id=member.user_id, tenant_id=self._tenant_id, role=Role(member.role)
                )
            except ValueError:
                continue
            if self._policy.scope_for(principal, Permission.AI_REQUEST) is None:
                continue
            if self._repo.exists_for_day(
                principal.user_id, ReportKind.OPERATIONS_DIGEST, ReportTrigger.DAILY, today
            ):
                continue
            snapshot = self._findings.snapshot_for(principal)
            if not snapshot.findings:
                continue  # nothing to explain; no empty daily digests
            _, was_created = self._create(
                principal,
                ReportKind.OPERATIONS_DIGEST,
                ReportTrigger.DAILY,
                self._digest_payload(snapshot, None),
            )
            created += int(was_created)
        return created

    def _digest_payload(self, snap: FindingsSnapshot, language: str | None) -> dict[str, object]:
        return {
            "language": validate_language(language or self._language),
            "thresholds": snap.thresholds,
            "counts": snap.counts,
            "findings": snap.findings[:MAX_FINDINGS_SENT],
        }

    # --- KPI explanation (AI Business Analyst) ----------------------------------

    def request_kpi_explanation(
        self,
        principal: Principal,
        *,
        period: str = "month",
        day: date | None = None,
        language: str | None = None,
    ) -> tuple[AiReport, bool]:
        """Explain company KPIs of the period containing ``day`` versus the period before.

        All values and changes are computed by the CRM; requires reports.view and ai.request.
        """
        self._require_ai(principal)
        if period not in PERIODS:
            raise DomainValidationError("period must be day, week or month.")
        start = period_start(period, day or datetime.now(UTC).date())
        current = self._kpis.readings(principal, period, start)
        previous = self._kpis.readings(principal, period, previous_start(period, start))
        payload = build_payload(
            language=validate_language(language or self._language),
            period=period,
            start=start,
            current=current,
            previous=previous,
        )
        return self._create(principal, ReportKind.KPI_EXPLANATION, ReportTrigger.MANUAL, payload)

    # --- reading ----------------------------------------------------------------

    def latest(self, principal: Principal, kind: ReportKind) -> AiReport:
        """The principal's own newest report of this kind."""
        report = self._repo.latest_for_user(principal.user_id, kind)
        if report is None:
            raise NotFoundError("No report of this kind yet.")
        return report

    # --- worker steps (idempotent) ------------------------------------------------

    def submit(self, report_id: UUID) -> None:
        report = self._repo.get(report_id)
        if report is None or report.status is not InsightStatus.PENDING:
            return
        try:
            state = self._client.submit(
                tenant_slug=self._tenant_slug,
                subject=f"user:{report.requested_by}",
                job_type=report.job_type,
                payload=report.payload,
                idempotency_key=f"crm-report-{report.id}",
            )
        except PermanentError:
            logger.warning("ai_report_submit_rejected", extra={"report_id": str(report.id)})
            with self._uow:
                self._finish(report, report.mark_failed("submit_rejected"))
            return
        with self._uow:
            version = report.version
            report.mark_submitted(state.job_id)
            self._repo.save(report, expected_version=version)
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug, report_id=report.id, delay_seconds=2.0
            )

    def poll(self, report_id: UUID) -> None:
        report = self._repo.get(report_id)
        if report is None or report.status is not InsightStatus.SUBMITTED or not report.ai_job_id:
            return
        try:
            state = self._client.fetch(
                tenant_slug=self._tenant_slug,
                subject=f"user:{report.requested_by}",
                job_id=report.ai_job_id,
            )
        except PermanentError:
            with self._uow:
                self._finish(report, report.mark_failed("fetch_rejected"))
            return
        if not state.is_terminal:
            age = (datetime.now(UTC) - report.created_at).total_seconds()
            if age > self._deadline:
                with self._uow:
                    self._finish(report, report.mark_failed("timeout"))
                return
            self._scheduler.schedule_poll(
                tenant_slug=self._tenant_slug, report_id=report.id, delay_seconds=poll_delay(age)
            )
            return
        with self._uow:
            if state.status == "succeeded" and state.output is not None:
                generator = state.output.get("generator")
                try:
                    event = report.mark_succeeded(
                        result=state.output,
                        schema_id=state.schema_id or "",
                        provider=state.provider or "unknown",
                        model=state.model or "unknown",
                        # Unknown provenance is treated as mock, never as a real model.
                        is_mock=not isinstance(generator, dict)
                        or generator.get("is_mock") is not False,
                    )
                except DomainValidationError:
                    event = report.mark_failed("unsupported_result")
            else:
                event = report.mark_failed(state.error_code or state.status)
            self._finish(report, event)

    # --- helpers ----------------------------------------------------------------

    def _require_ai(self, principal: Principal) -> None:
        if not self._enabled:
            raise AiDisabledError("AI features are not enabled for this installation.")
        self._policy.require(principal, Permission.AI_REQUEST)

    def _create(
        self,
        principal: Principal,
        kind: ReportKind,
        trigger: ReportTrigger,
        payload: dict[str, object],
    ) -> tuple[AiReport, bool]:
        report = AiReport.request(
            kind=kind, requested_by=principal.user_id, trigger=trigger, payload=dict(payload)
        )
        with self._uow:
            existing = self._repo.find_reusable(principal.user_id, kind, report.payload_hash)
            if existing is not None and trigger is ReportTrigger.MANUAL:
                return existing, False
            self._repo.add(report)
            self._record(principal.user_id, f"ai_report.{kind.value}_requested", report)
            self._scheduler.schedule_submit(tenant_slug=self._tenant_slug, report_id=report.id)
        return report, True

    def _finish(self, report: AiReport, event: DomainEvent) -> None:
        self._repo.save(report, expected_version=report.version)
        self._outbox.add(event, aggregate_type=AGGREGATE, aggregate_id=str(report.id))
        self._record(report.requested_by, f"ai_report.{report.status.value}", report)

    def _record(self, actor: UUID, action: str, report: AiReport) -> None:
        self._audit.record(
            AuditEntry(
                actor_user_id=actor,
                action=action,
                resource_type=AGGREGATE,
                resource_id=str(report.id),
            )
        )
