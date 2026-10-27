# Sales Pipeline

**Status:** planned, not implemented. Only the package and this note exist.

**Plane:** Tenant plane.

**Responsibility:** Pipelines, stages, deals, deal amounts and win/loss outcomes.

**Expected domain events:** DealCreated, DealStageChanged, DealWon, DealLost.

**Collaborates with:** customers, sales_kpi (read models).

When implemented, follow the reference module `modules/customers`:
`domain/` (pure Python) -> `application/` (use cases, ports) -> `infrastructure/`
(Django app, ORM records, adapters) -> `presentation/` (DRF views), plus
`composition.py`. Register the Django app label in `settings.TENANCY` (control,
tenant or shared plane) or the classification test will fail.
