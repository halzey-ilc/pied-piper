# Leads

**Status:** planned, not implemented. Only the package and this note exist.

**Plane:** Tenant plane.

**Responsibility:** Lead capture, qualification, assignment and conversion to customers/deals.

**Expected domain events:** LeadCaptured, LeadQualified, LeadConverted.

**Collaborates with:** customers, sales_pipeline, integrations (inbound channels).

When implemented, follow the reference module `modules/customers`:
`domain/` (pure Python) -> `application/` (use cases, ports) -> `infrastructure/`
(Django app, ORM records, adapters) -> `presentation/` (DRF views), plus
`composition.py`. Register the Django app label in `settings.TENANCY` (control,
tenant or shared plane) or the classification test will fail.
