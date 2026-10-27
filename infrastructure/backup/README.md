# Backup & restore (design)

Not wired into the local stack. This records the intended approach; see
[`docs/operations/backup-restore.md`](../../docs/operations/backup-restore.md)
for commands.

- **Tool:** [pgBackRest](https://pgbackrest.org/) for PostgreSQL — full +
  differential backups and WAL archiving for point-in-time recovery.
- **Per-tenant granularity:** because each company has its own database,
  backup and restore are per-tenant. The stanza layout groups stanzas by
  cluster; a tenant can be restored without touching others.
- **What must be backed up:** every tenant database **and** the control-plane
  database (the tenant registry is required to make tenant backups meaningful).
- **Not backed up:** Redis (cache / transient results only) and RabbitMQ
  (durable queues, but business state is reconstructable from the outbox).
- **Verification:** a restore is only real once test-restored; schedule periodic
  restore drills into a scratch cluster.

Claiming "backups work" requires a tested restore. This repository ships the
design and configuration layout only.
