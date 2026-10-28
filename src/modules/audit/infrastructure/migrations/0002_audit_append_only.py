"""Enforce audit-log immutability in the database itself.

The application already refuses to UPDATE or DELETE an ``AuditRecord`` (the model
overrides ``save``/``delete``), but that guard lives in the process and can be
bypassed by anything that reaches the table directly. This migration adds a
``BEFORE UPDATE OR DELETE`` trigger so the database rejects both operations for
every connection, including a compromised application role or an ad-hoc psql
session. INSERT (append) and SELECT remain allowed. The trigger is created in
every database the ``audit`` app is migrated into (control plane and each tenant
database), because audit is a shared app.

Operational note: TRUNCATE is not fired by row-level triggers; the least-privilege
application role must therefore not hold the TRUNCATE privilege on ``audit_record``
(see infrastructure/postgres/init and docs/operations/production-hardening.md).
"""

from __future__ import annotations

from django.db import migrations

_FORWARD = """
CREATE OR REPLACE FUNCTION audit_record_reject_mutation() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_record is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

DROP TRIGGER IF EXISTS audit_record_append_only ON audit_record;

CREATE TRIGGER audit_record_append_only
    BEFORE UPDATE OR DELETE ON audit_record
    FOR EACH ROW EXECUTE FUNCTION audit_record_reject_mutation();
"""

_REVERSE = """
DROP TRIGGER IF EXISTS audit_record_append_only ON audit_record;
DROP FUNCTION IF EXISTS audit_record_reject_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=_FORWARD, reverse_sql=_REVERSE),
    ]
