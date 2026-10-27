# Activities

**Status:** planned, not implemented. Only the package and this note exist.

**Plane:** Tenant plane.

**Responsibility:** Tasks, calls, notes and the activity timeline of customers and deals.

**Expected domain events:** ActivityLogged, TaskCompleted.

**Collaborates with:** customers, sales_pipeline, telephony.

When implemented, follow the reference module `modules/customers`:
`domain/` (pure Python) -> `application/` (use cases, ports) -> `infrastructure/`
(Django app, ORM records, adapters) -> `presentation/` (DRF views), plus
`composition.py`. Register the Django app label in `settings.TENANCY` (control,
tenant or shared plane) or the classification test will fail.
