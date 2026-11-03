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

## Automatic morning/evening reports (in-app)

Beat task `notifications.daily_reports` (every 5 minutes) sends, per company, at the
company-local times set in `PATCH /api/v1/company/` (`morning_report_*`,
`evening_report_*`; defaults 09:00 / 19:00, director only):

- morning (`report`): manager - tasks due today, overdue, meetings today, deals without a
  next step (link `/my-day`); head of sales - department summary per manager, who has not
  started the shift (if the company uses work sessions), who is absent (`/team`);
  director - company summary per department (`/team`);
- evening (`report`): calls, meetings held/no-show, tasks done, deals moved/lost, payments
  recorded and confirmed (money = confirmed only, company currency), new leads; managers
  without activity are listed (`/my-day`, `/daily-reports`), and their head of sales - or
  the director when the department has no head - gets an `inactivity` alert (`/team`).

A report is sent from its time until 3 hours later. Absent, dismissed and deactivated
members get nothing. Once per user per local day: keyed notifications plus a per-company
run marker (`notifications_daily_report_run`). Email/Telegram delivery is not connected.

## Preferences

Each user can turn categories (meeting, deal, lead, payment, system, ai, task, report,
inactivity) on or off;
everything is on by default.

## API (your own notifications)

`GET /api/v1/notifications/` (with unread count), `GET .../unread-count/`,
`POST .../{id}/read/`, `POST .../read-all/`,
`GET/POST .../preferences/`. Tenant-scoped; each user sees only their own.

> Email/push delivery and scheduled meeting-reminder delivery are out of scope
> here (channels and a reminder scan are the integration points).
