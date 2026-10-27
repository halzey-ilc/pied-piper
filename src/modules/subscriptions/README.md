# Subscriptions

**Status:** planned, not implemented. Only the package and this note exist.

**Plane:** Control plane.

**Responsibility:** Plans, limits and billing state of organizations (tenants).

**Expected domain events:** SubscriptionStarted, LimitExceeded.

**Collaborates with:** organizations, tenant_registry.

When implemented, follow the reference module `modules/customers`:
`domain/` (pure Python) -> `application/` (use cases, ports) -> `infrastructure/`
(Django app, ORM records, adapters) -> `presentation/` (DRF views), plus
`composition.py`. Register the Django app label in `settings.TENANCY` (control,
tenant or shared plane) or the classification test will fail.
