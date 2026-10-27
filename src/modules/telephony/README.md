# Telephony

**Status:** planned, not implemented. Only the package and this note exist.

**Plane:** Tenant plane.

**Responsibility:** Call records, recordings metadata and click-to-call through provider adapters.

**Expected domain events:** CallStarted, CallEnded.

**Collaborates with:** integrations (telephony provider spec), activities.

When implemented, follow the reference module `modules/customers`:
`domain/` (pure Python) -> `application/` (use cases, ports) -> `infrastructure/`
(Django app, ORM records, adapters) -> `presentation/` (DRF views), plus
`composition.py`. Register the Django app label in `settings.TENANCY` (control,
tenant or shared plane) or the classification test will fail.
