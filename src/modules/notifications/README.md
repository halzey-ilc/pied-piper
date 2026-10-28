# Notifications

**Status:** implemented (tenant plane).

In-app notifications for users, built from domain events, with per-user
preferences.

## How notifications are created

Event handlers (`infrastructure/handlers.py`, registered at app ready) turn
events into notifications for the relevant user:

- `meetings.meeting_scheduled` -> **Meeting** notification for the owner
- `sales.deal_created` -> **Deal** notification for the owner
- `leads.lead_captured` -> **Lead** notification for the owner

Handlers run in the event dispatcher's unit of work, are idempotent (unique
`recipient + source_event_id`) and skip categories the user has muted.

## Preferences

Each user can turn categories (meeting, deal, lead, payment, system) on or off;
everything is on by default.

## API (your own notifications)

`GET /api/v1/notifications/` (with unread count), `GET .../unread-count/`,
`POST .../{id}/read/`, `POST .../read-all/`,
`GET/POST .../preferences/`. Tenant-scoped; each user sees only their own.

> Email/push delivery and scheduled meeting-reminder delivery are out of scope
> here (channels and a reminder scan are the integration points).
