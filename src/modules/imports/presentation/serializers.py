from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.imports.application.ports import ImportJob
from modules.imports.application.service import Preview
from modules.imports.domain.rows import MAX_TEXT_BYTES, ImportEntity


class ImportRequestSerializer(serializers.Serializer[dict[str, Any]]):
    entity = serializers.ChoiceField(choices=[e.value for e in ImportEntity])
    filename = serializers.CharField(max_length=200, required=False, allow_blank=True)
    # The CSV text as read in the browser (UTF-8); a few characters of headroom for JSON.
    content = serializers.CharField(max_length=MAX_TEXT_BYTES, trim_whitespace=False)
    owner_user_id = serializers.UUIDField(required=False, allow_null=True)


def preview_to_dict(preview: Preview) -> dict[str, Any]:
    from modules.imports.application.service import RowStatus

    return {
        "entity": preview.entity.value,
        "total": len(preview.rows),
        "new": preview.count(RowStatus.NEW),
        "duplicates": preview.count(RowStatus.DUPLICATE),
        "invalid": preview.count(RowStatus.INVALID),
        "rows": [
            {
                "line": item.row.line,
                "name": item.row.name,
                "phone": item.row.phone,
                "email": item.row.email,
                "source": item.row.source,
                "status": item.status.value,
                "reason": item.reason,
                "duplicate_of": None
                if item.duplicate_of is None
                else {
                    "kind": item.duplicate_of.kind,
                    "id": str(item.duplicate_of.id),
                    "name": item.duplicate_of.name,
                },
                "duplicate_line": item.duplicate_line,
            }
            for item in preview.rows
        ],
    }


def job_to_dict(job: ImportJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "entity": job.entity.value,
        "filename": job.filename,
        "created_by": str(job.created_by),
        "owner_user_id": str(job.owner_user_id),
        "total": job.total,
        "created": job.created,
        "duplicates": job.duplicates,
        "invalid": job.invalid,
        "failed": job.failed,
        "errors": [{"line": e.line, "message": e.message} for e in job.errors],
        "created_at": job.created_at.isoformat(),
    }
