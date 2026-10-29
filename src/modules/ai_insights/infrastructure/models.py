from __future__ import annotations

from django.db import models

from modules.ai_insights.domain.insight import InsightKind, InsightStatus
from modules.ai_insights.domain.report import ReportKind, ReportTrigger


class CallInsightRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    call_id = models.UUIDField()
    kind = models.CharField(max_length=32, choices=[(k.value, k.name.title()) for k in InsightKind])
    status = models.CharField(
        max_length=12, choices=[(s.value, s.name.title()) for s in InsightStatus]
    )
    requested_by = models.UUIDField()
    # sha256 of the analysed text, never the text itself.
    source_hash = models.CharField(max_length=64)
    ai_job_id = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    result = models.JSONField(null=True, blank=True)
    schema_id = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    provider = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    model = models.CharField(max_length=128, null=True, blank=True)  # noqa: DJ001
    is_mock = models.BooleanField(default=False)
    error_code = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_insights_call_insight"
        indexes = [
            models.Index(fields=["call_id", "kind", "created_at"], name="call_insight_call_idx"),
            models.Index(fields=["status"], name="call_insight_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.call_id} ({self.status})"


class AiReportRecord(models.Model):
    """AI explanations of CRM-computed numbers (operations digests, KPI explanations)."""

    id = models.UUIDField(primary_key=True, editable=False)
    kind = models.CharField(
        max_length=32,
        choices=[(k.value, k.name.title()) for k in ReportKind],
        default=ReportKind.OPERATIONS_DIGEST.value,
    )
    requested_by = models.UUIDField()
    trigger = models.CharField(
        max_length=8, choices=[(t.value, t.name.title()) for t in ReportTrigger]
    )
    report_date = models.DateField()
    # The exact job input: ids, kinds, numbers and thresholds only (no names or text).
    payload = models.JSONField()
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=12, choices=[(s.value, s.name.title()) for s in InsightStatus]
    )
    ai_job_id = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    result = models.JSONField(null=True, blank=True)
    provider = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    model = models.CharField(max_length=128, null=True, blank=True)  # noqa: DJ001
    is_mock = models.BooleanField(default=False)
    error_code = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_insights_report"
        constraints = [
            # At most one daily report per kind, user and day, even if the beat job runs twice.
            models.UniqueConstraint(
                fields=["kind", "requested_by", "report_date"],
                condition=models.Q(trigger="daily"),
                name="ai_report_one_daily_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["requested_by", "kind", "created_at"], name="ai_report_user_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.requested_by} {self.report_date} ({self.status})"


class ReplySuggestionRecord(models.Model):
    """AI classification and reply draft for a conversation's latest customer message."""

    id = models.UUIDField(primary_key=True, editable=False)
    conversation_id = models.UUIDField()
    requested_by = models.UUIDField()
    language = models.CharField(max_length=2)
    # sha256 of the job input (message texts are not copied here).
    source_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=12, choices=[(s.value, s.name.title()) for s in InsightStatus]
    )
    ai_job_id = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    result = models.JSONField(null=True, blank=True)
    provider = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    model = models.CharField(max_length=128, null=True, blank=True)  # noqa: DJ001
    is_mock = models.BooleanField(default=False)
    error_code = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_insights_reply_suggestion"
        indexes = [
            models.Index(
                fields=["conversation_id", "created_at"], name="reply_suggestion_conv_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"reply {self.conversation_id} ({self.status})"
