# Payments

**Status:** planned, not implemented. Only the package and this note exist.

**Plane:** Tenant plane.

**Responsibility:** Invoices and payment status of deals (no card data is ever stored here).

**Expected domain events:** InvoiceIssued, PaymentReceived.

**Collaborates with:** sales_pipeline, integrations (payment providers).

When implemented, follow the reference module `modules/customers`:
`domain/` (pure Python) -> `application/` (use cases, ports) -> `infrastructure/`
(Django app, ORM records, adapters) -> `presentation/` (DRF views), plus
`composition.py`. Register the Django app label in `settings.TENANCY` (control,
tenant or shared plane) or the classification test will fail.
