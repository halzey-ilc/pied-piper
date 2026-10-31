from __future__ import annotations

from django.db import models
from django.db.models import Q

from modules.planning.domain.absence import AbsenceKind
from modules.planning.domain.plan import PlanMetric, PlanScope


class PlanRecord(models.Model):
    """A target for one metric over one period at a company/department/manager level.
    Lives in each tenant's own database."""

    id = models.UUIDField(primary_key=True, editable=False)
    scope = models.CharField(max_length=10, choices=[(s.value, s.name.title()) for s in PlanScope])
    scope_id = models.UUIDField(null=True, blank=True)
    metric = models.CharField(
        max_length=20, choices=[(m.value, m.name.title()) for m in PlanMetric]
    )
    period = models.CharField(max_length=10)  # YYYY-MM, YYYY-Www or YYYY-MM-DD
    target = models.DecimalField(max_digits=16, decimal_places=2)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "planning_plan"
        indexes = [
            models.Index(fields=["scope", "scope_id"], name="plan_scope_idx"),
            models.Index(fields=["metric", "period"], name="plan_metric_period_idx"),
        ]
        constraints = [
            # One plan per (scope, scope_id, metric, period). Company plans have a
            # null scope_id; the service also guards against duplicates there.
            models.UniqueConstraint(
                fields=["scope", "scope_id", "metric", "period"],
                condition=Q(scope_id__isnull=False),
                name="plan_unique_scoped",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.scope}:{self.metric}:{self.period}"


class PlanChangeRecord(models.Model):
    """Append-only history of a plan's target changes."""

    id = models.UUIDField(primary_key=True, editable=False)
    plan = models.ForeignKey(PlanRecord, on_delete=models.CASCADE, related_name="changes")
    author_user_id = models.UUIDField()
    old_target = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    new_target = models.DecimalField(max_digits=16, decimal_places=2)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "planning_plan_change"
        indexes = [models.Index(fields=["plan", "created_at"], name="plan_change_idx")]
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.plan_id}: {self.new_target}"


class AbsenceRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    user_id = models.UUIDField()
    kind = models.CharField(max_length=14, choices=[(k.value, k.name.title()) for k in AbsenceKind])
    start_date = models.DateField()
    end_date = models.DateField()
    comment = models.CharField(max_length=500, null=True, blank=True)  # noqa: DJ001
    created_by = models.UUIDField()
    team_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "planning_absence"
        indexes = [
            models.Index(fields=["user_id", "start_date"], name="absence_user_idx"),
            models.Index(fields=["start_date", "end_date"], name="absence_window_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}: {self.kind} {self.start_date}..{self.end_date}"
