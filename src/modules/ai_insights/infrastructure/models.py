from __future__ import annotations

from django.db import models

from modules.ai_insights.domain.digest import DigestTrigger
from modules.ai_insights.domain.insight import InsightKind, InsightStatus


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


class OperationsDigestRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    requested_by = models.UUIDField()
    trigger = models.CharField(
        max_length=8, choices=[(t.value, t.name.title()) for t in DigestTrigger]
    )
    digest_date = models.DateField()
    # The exact job input: refs ("lead:<uuid>"), kinds, ages and thresholds only.
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
        db_table = "ai_insights_operations_digest"
        constraints = [
            # At most one daily digest per user and day, even if the beat job runs twice.
            models.UniqueConstraint(
                fields=["requested_by", "digest_date"],
                condition=models.Q(trigger="daily"),
                name="ops_digest_one_daily_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["requested_by", "created_at"], name="ops_digest_user_idx"),
        ]

    def __str__(self) -> str:
        return f"digest {self.requested_by} {self.digest_date} ({self.status})"
