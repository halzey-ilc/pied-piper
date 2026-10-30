from __future__ import annotations

from django.db import models
from django.db.models import Q

from modules.sales_pipeline.domain.deal import DealStatus
from modules.sales_pipeline.domain.pipeline import StageKind


class PipelineRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    name = models.CharField(max_length=100)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "sales_pipeline"
        constraints = [
            # At most one default pipeline per tenant database.
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="sales_pipeline_one_default",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class StageRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    pipeline = models.ForeignKey(PipelineRecord, on_delete=models.CASCADE, related_name="stages")
    name = models.CharField(max_length=100)
    position = models.PositiveSmallIntegerField()
    probability = models.PositiveSmallIntegerField(default=0)
    kind = models.CharField(
        max_length=4,
        choices=[(k.value, k.name.title()) for k in StageKind],
        default=StageKind.OPEN.value,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "sales_stage"
        constraints = [
            models.UniqueConstraint(fields=["pipeline", "position"], name="sales_stage_position"),
            models.UniqueConstraint(fields=["pipeline", "name"], name="sales_stage_name"),
        ]
        ordering = ["position"]

    def __str__(self) -> str:
        return f"{self.name} (#{self.position})"


class LossReasonRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "sales_loss_reason"
        constraints = [
            models.UniqueConstraint(fields=["name"], name="sales_loss_reason_name"),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DealRecord(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    title = models.CharField(max_length=200)
    pipeline = models.ForeignKey(PipelineRecord, on_delete=models.PROTECT, related_name="deals")
    stage = models.ForeignKey(StageRecord, on_delete=models.PROTECT, related_name="deals")
    status = models.CharField(
        max_length=8,
        choices=[(s.value, s.name.title()) for s in DealStatus],
        default=DealStatus.OPEN.value,
    )
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    currency = models.CharField(max_length=3)
    customer_id = models.UUIDField(null=True, blank=True)
    owner_user_id = models.UUIDField()
    team_id = models.UUIDField(null=True, blank=True)
    source = models.CharField(max_length=100, null=True, blank=True)  # noqa: DJ001
    next_step = models.CharField(max_length=500, null=True, blank=True)  # noqa: DJ001
    expected_close_date = models.DateField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    lost_reason = models.CharField(max_length=500, null=True, blank=True)  # noqa: DJ001
    loss_reason = models.ForeignKey(
        LossReasonRecord,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="deals",
    )
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sales_deal"
        indexes = [
            models.Index(fields=["status"], name="deal_status_idx"),
            models.Index(fields=["pipeline", "stage"], name="deal_pipeline_stage_idx"),
            models.Index(fields=["owner_user_id"], name="deal_owner_idx"),
            models.Index(fields=["team_id"], name="deal_team_idx"),
            models.Index(fields=["customer_id"], name="deal_customer_idx"),
            models.Index(fields=["created_at"], name="deal_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class DealStageChangeRecord(models.Model):
    """Append-only history of a deal's stage transitions."""

    id = models.UUIDField(primary_key=True, editable=False)
    deal = models.ForeignKey(DealRecord, on_delete=models.CASCADE, related_name="stage_changes")
    from_stage_id = models.UUIDField(null=True, blank=True)
    to_stage_id = models.UUIDField()
    author_user_id = models.UUIDField()
    deal_version = models.PositiveIntegerField()
    reason = models.CharField(max_length=500, null=True, blank=True)  # noqa: DJ001
    loss_reason_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "sales_deal_stage_change"
        indexes = [
            models.Index(fields=["deal", "created_at"], name="deal_stage_change_idx"),
        ]
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.deal_id}: -> {self.to_stage_id}"
