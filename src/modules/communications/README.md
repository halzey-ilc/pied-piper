# Communications

**Status:** implemented (tenant plane).

Conversations and messages over messaging channels (WhatsApp, Telegram,
Instagram, e-mail).

## Inbound (the real event path)

The integrations layer verifies and stores a provider webhook (signature +
dedup via the inbox) and queues `integrations.process_webhook`. That task
(`entrypoints/workers/tasks/integrations.py`) reads the payload, turns it into a
normalized message (`infrastructure/parsers.py`, per provider) and appends it to
a conversation via `ingest_inbound` — idempotent on the provider message id. A
new contact starts a new (unassigned) conversation; agents pick it up with
**assign**. A `communications.message_received` event is emitted.

> Live inbound requires a real provider: a bot token / app secret **and a
> public webhook URL** the provider can reach (the local stack is not publicly
> reachable). The parsing and ingest are fully implemented and tested with
> simulated payloads; only the live connection needs your credentials + tunnel.

## Outbound

`send_message` appends an outbound message and sends it through a `ChannelSender`
(a stub in the scaffold; a real adapter would use the integrations provider
adapters) and emits `communications.message_sent`.

## API

`GET /api/v1/communications/conversations/` and `.../{id}/`,
`GET/POST .../{id}/messages/`, `POST .../{id}/assign|read|close|reopen/`.
Tenant-scoped, RBAC (`communications.view` / `communications.send`, own/team/all;
unassigned conversations are visible so they can be claimed).
