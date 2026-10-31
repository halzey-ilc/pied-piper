"""Data migration: pre-P0-3 meetings used status 'completed' for a meeting that
took place; P0-3 renames that outcome to 'held'. Reversible."""

from __future__ import annotations

from django.db import migrations


def _completed_to_held(apps, schema_editor):
    Meeting = apps.get_model("meetings", "MeetingRecord")
    db = schema_editor.connection.alias
    Meeting.objects.using(db).filter(status="completed").update(status="held")


def _held_to_completed(apps, schema_editor):
    Meeting = apps.get_model("meetings", "MeetingRecord")
    db = schema_editor.connection.alias
    Meeting.objects.using(db).filter(status="held").update(status="completed")


class Migration(migrations.Migration):
    dependencies = [
        ("meetings", "0002_meetingrecord_booked_by_user_id_and_more"),
    ]

    operations = [
        migrations.RunPython(_completed_to_held, _held_to_completed),
    ]
