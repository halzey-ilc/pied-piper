# Backup & restore

> Design and intended commands. Not wired into the local stack; no backups run
> here. "Backups work" is only true once a restore has been tested.

## Scope

Back up **every tenant database and the control-plane database**. The tenant
registry (control plane) is required to interpret tenant backups, so the two are
backed up together. Redis and RabbitMQ are not backed up (cache / reconstructable
from the outbox).

## Tool: pgBackRest

Full + differential backups and WAL archiving for point-in-time recovery.
Because each company has its own database, backup/restore is naturally
per-tenant.

### Example stanza (per cluster)

```ini
[global]
repo1-path=/var/lib/pgbackrest
repo1-retention-full=7
start-fast=y

[crm-primary]
pg1-path=/var/lib/postgresql/data
```

### Commands (illustrative)

```bash
pgbackrest --stanza=crm-primary stanza-create
pgbackrest --stanza=crm-primary --type=full backup
pgbackrest --stanza=crm-primary --type=diff backup      # scheduled

# Restore one tenant database to a point in time into a scratch server, verify,
# then promote. Never restore straight over a live tenant without a snapshot.
pgbackrest --stanza=crm-primary --type=time \
  "--target=2026-01-01 12:00:00+00" --db-include=crm_tenant_demo restore
```

## Restore drills

Schedule periodic restores into a throwaway cluster and run the application's
readiness checks and a smoke test against the restored tenant. Record the RPO
(WAL archive interval) and RTO (measured restore time) achieved.

## Responsibilities before production

- Encrypted off-site repository; access controlled and audited.
- Automated schedule (full weekly, diff daily, continuous WAL) with alerting on
  failed or stale backups.
- Documented, rehearsed DR runbook with named owners.
