"""Generalise operations digests into AI reports (adds KPI explanations).

Non-destructive: the existing table and rows are renamed and kept; existing rows
become kind=operations_digest.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai_insights", "0002_operationsdigestrecord"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="operationsdigestrecord", name="ops_digest_one_daily_per_user"
        ),
        migrations.RemoveIndex(model_name="operationsdigestrecord", name="ops_digest_user_idx"),
        migrations.RenameModel(old_name="OperationsDigestRecord", new_name="AiReportRecord"),
        migrations.AlterModelTable(name="aireportrecord", table="ai_insights_report"),
        migrations.RenameField(
            model_name="aireportrecord", old_name="digest_date", new_name="report_date"
        ),
        migrations.AddField(
            model_name="aireportrecord",
            name="kind",
            field=models.CharField(
                choices=[
                    ("operations_digest", "Operations_Digest"),
                    ("kpi_explanation", "Kpi_Explanation"),
                ],
                default="operations_digest",
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="aireportrecord",
            constraint=models.UniqueConstraint(
                condition=models.Q(("trigger", "daily")),
                fields=("kind", "requested_by", "report_date"),
                name="ai_report_one_daily_per_user",
            ),
        ),
        migrations.AddIndex(
            model_name="aireportrecord",
            index=models.Index(
                fields=["requested_by", "kind", "created_at"], name="ai_report_user_idx"
            ),
        ),
    ]
