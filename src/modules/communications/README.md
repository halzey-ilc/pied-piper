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

## WhatsApp

Built for the Cloud API webhook format, including Coexistence (the number stays
in the WhatsApp Business app). Not yet verified against a live account.

- **Webhook URL:** `https://<domain>/api/v1/webhooks/whatsapp/<tenant-slug>/`. Meta
  verifies it with a GET (`WHATSAPP_VERIFY_TOKEN`); events are POSTs signed with
  `WHATSAPP_APP_SECRET`. Subscribe to `messages` and, for Coexistence,
  `smb_message_echoes`.
- **Leads:** the first message links the conversation to the newest lead and
  customer with that phone. If there is neither, a lead (source `whatsapp`) is
  created for `WHATSAPP_LEAD_OWNER_USER_ID`; with no owner set, the message is
  kept and the link is retried on the next message.
- **Replies from the phone** arrive as echoes and appear as sent messages.
- **Media** is not downloaded: the chat shows a label such as `[Фото]` plus any
  caption.

## Outbound

`send_message` appends an outbound message, sends it through a `ChannelSender` and
emits `communications.message_sent`. WhatsApp uses the Cloud API once
`WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` are set. Until then, and for
every other channel, the stub reports success **without delivering anything**.

A rejected send returns 409 and stores nothing. WhatsApp can also accept a message
and fail it later (for example outside the 24-hour window, which needs an approved
template; templates are not implemented): a `failed` status webhook then marks the
message `failed`.

## API

`GET /api/v1/communications/conversations/` and `.../{id}/`,
`GET/POST .../{id}/messages/`, `POST .../{id}/assign|read|close|reopen/`.
Tenant-scoped, RBAC (`communications.view` / `communications.send`, own/team/all;
unassigned conversations are visible so they can be claimed).
