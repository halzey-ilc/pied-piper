# Notifications

**Status:** planned, not implemented. Only the package and this note exist.

**Plane:** Tenant plane.

**Responsibility:** User notifications (in-app, e-mail, push) with preferences and digests.

**Expected domain events:** NotificationRequested.

**Collaborates with:** every module via domain events; runs in the notifications queue.

When implemented, follow the reference module `modules/customers`:
`domain/` (pure Python) -> `application/` (use cases, ports) -> `infrastructure/`
(Django app, ORM records, adapters) -> `presentation/` (DRF views), plus
`composition.py`. Register the Django app label in `settings.TENANCY` (control,
tenant or shared plane) or the classification test will fail.
