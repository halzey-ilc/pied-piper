# Communications

**Status:** planned, not implemented. Only the package and this note exist.

**Plane:** Tenant plane.

**Responsibility:** Conversations over WhatsApp, Telegram, Instagram and e-mail; message history.

**Expected domain events:** MessageReceived, MessageSent.

**Collaborates with:** integrations (ChannelAdapter port, webhooks).

When implemented, follow the reference module `modules/customers`:
`domain/` (pure Python) -> `application/` (use cases, ports) -> `infrastructure/`
(Django app, ORM records, adapters) -> `presentation/` (DRF views), plus
`composition.py`. Register the Django app label in `settings.TENANCY` (control,
tenant or shared plane) or the classification test will fail.
