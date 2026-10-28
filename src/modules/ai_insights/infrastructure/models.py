from __future__ import annotations

from django.db import models

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
