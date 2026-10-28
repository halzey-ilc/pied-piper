"""The database itself rejects mutations of the audit log.

These assertions bypass the ORM's application-level guard and go straight to SQL,
so they prove the trigger from migration ``0002_audit_append_only`` holds for any
connection, not just the Django model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from django.db import DatabaseError, connections, transaction

from modules.audit.infrastructure.models import AuditRecord

pytestmark = pytest.mark.django_db(databases=["tenant_acme"])
ALIAS = "tenant_acme"


def _seed() -> str:
    record = AuditRecord(
        id=uuid4(),
        occurred_at=datetime.now(UTC),
        action="customer.created",
        resource_type="customer",
        resource_id="c-1",
    )
    record.save(using=ALIAS)
    return str(record.id)


def test_raw_update_is_rejected_by_the_database() -> None:
    record_id = _seed()
    with (
        pytest.raises(DatabaseError, match="append-only"),
        transaction.atomic(using=ALIAS),
        connections[ALIAS].cursor() as cursor,
    ):
        cursor.execute("UPDATE audit_record SET action = %s WHERE id = %s", ["tampered", record_id])
    assert AuditRecord.objects.using(ALIAS).get(id=record_id).action == "customer.created"


def test_raw_delete_is_rejected_by_the_database() -> None:
    record_id = _seed()
    with (
        pytest.raises(DatabaseError, match="append-only"),
        transaction.atomic(using=ALIAS),
        connections[ALIAS].cursor() as cursor,
    ):
        cursor.execute("DELETE FROM audit_record WHERE id = %s", [record_id])
    assert AuditRecord.objects.using(ALIAS).filter(id=record_id).exists()
