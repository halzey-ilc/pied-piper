# P0 implementation report — CRM Axlon backend

Status of the agreed **P0** scope from [IMPLEMENTATION_AUDIT.md](IMPLEMENTATION_AUDIT.md)
and [API_CONTRACT.md](API_CONTRACT.md). Nothing is merged to `main`; each stage is on
its own branch, pushed to `origin` (`halzey-ilc/pied-piper`).

## 1. Branches and commits

| Stage | Branch (pushed) | Commits |
|---|---|---|
| audit | `audit/implementation-audit` | `a2ec0b5` |
| P0-1 roles/departments/staff/company | `feature/p0-core` | `c5e5ae0` |
| P0-2 deals / customers / leads | `feature/p0-2-deals` | `b6f5b17` stage kinds · `f2a479d` loss reasons · `8befe4f` stage history · `acb4b4f` source/next_step · `ac52373` custom fields · `0627272` search/filters/sort/attention · `b59a727` customer search + lead dup/convert |
| P0-3 tasks / meetings / payments / planning | `feature/p0-3-ops` (off P0-2) | `5b8259d` tasks · `3ecd4d5` meetings · `9eaf688` payments · `5e9621f` planning |
| P0-4 head-of-sales cabinet | `feature/p0-4-cabinet` (off P0-3) | `828a087` today/inactivity/traffic-light · `e559b24` plan-fact |

`feature/p0-4-cabinet` is cumulative: it contains all P0 work.

## 2. What is implemented

**P0-1 — organisation (prior session, verified here).** Roles director/head_of_sales/
lead_caller/operations added alongside the kept owner/admin/manager/sales_rep/viewer
(mapped via `product_role`); departments; staff accounts with one-time temporary
password and forced change; one active company per user; company country/currency
(KGS)/timezone (Asia/Bishkek); `/me` extended.

**P0-2 — deals, customers, leads.**
- Stage kinds open/won/lost + `is_active`; a won/lost-kind transition closes the deal.
- Loss reason catalog (deactivate, not delete); lost deals carry `loss_reason_id`.
- Append-only deal stage-change history.
- Deal `source` and `next_step`.
- Configurable custom fields (9 types, select options, required) validated on write.
- Deal search / filters (manager, customer, created range) / ordering / `attention`
  (overdue, no_next_step) in the company timezone.
- Customer search (name/email/phone-digits); lead duplicate candidates (leads +
  customers); lead convert with optional deal creation.

**P0-3 — operations.**
- Tasks: task_type, priority, assignee ≠ creator, result, cancelled, subject=lead;
  assignment needs management scope; at most two open tasks per deal; is_overdue /
  due_group in the company timezone; complete/cancel/reassign.
- Meetings: outcomes held/no_show/rescheduled/cancelled, confirmation (manual or by
  response), format (office/online/visit), host/booked-by, duration.
- Payments as individual records (prepayment/additional/full, seller, refund,
  idempotent by reference); sales metrics — sales count, turnover, received, debt —
  kept as distinct figures.
- Planning module: sales plans (company/department/manager) with change history and
  period granularity validation; staff absences.

**P0-4 — head-of-sales cabinet** (read-only aggregation).
- `team/today/`: per-manager board + department totals + `as_of`, traffic light.
  APPROVED red rules (configurable thresholds): no activity on a working day, a
  meeting over 45 min old without an outcome, under 40% of morning meetings confirmed
  by 10:00. Yellow/green and the 0-100 day score are NOT approved → yellow off by
  default (red or green), a documented proposal behind a config flag.
- `team/inactivity/`: managers silent today and not on absence.
- `team/plan-fact/`: plan vs actual sales + received, working days, pace forecast.

## 3. New / changed API endpoints

- `GET/POST/PATCH staff/*`, `GET/POST/PATCH departments/`, `GET/PATCH company/`,
  `POST auth/password/change/`, extended `GET me/` (P0-1).
- `sales/`: stage kinds in pipeline create/read; `POST sales/deals/{id}/stage/`
  (won/lost/open with reason/loss_reason_id); `GET sales/deals/{id}/stage-history/`;
  `GET/POST sales/loss-reasons/`, `PATCH .../{id}/`; `GET/POST sales/custom-fields/`,
  `PATCH .../{id}/`; deal list query (`search`, `manager_id`, `customer_id`,
  `created_from/to`, `attention`, `ordering`); deal create/edit `source`/`next_step`/
  `custom_fields`.
- `customers/?search=`; `GET leads/{id}/duplicates/`; `POST leads/{id}/convert/`
  `{create_deal?}`.
- `activities/`: task fields on create; `POST activities/{id}/cancel/`, `.../reassign/`;
  `complete` accepts a result; read adds is_overdue/due_group.
- `meetings/`: `POST meetings/{id}/confirm/`, `POST meetings/{id}/outcome/`; create
  accepts format/host/booked_by; read adds confirmation/duration.
- `payments/`: `GET/POST payments/`, `GET payments/{id}/`, `GET payments/metrics/`
  (alongside the existing `payments/invoices/*`).
- `plans/` (GET list, PUT set), `GET plans/{id}/history/`, `GET/POST absences/`.
- `team/today/`, `team/inactivity/`, `team/plan-fact/`.

## 4. Migrations (all additive; no data loss)

organizations 0002 · identity 0004 (P0-1) · sales_pipeline 0002–0006 · activities 0002
· meetings 0002 (schema) + 0003 (data: completed→held, reversible) · payments 0002 ·
planning 0001. The one-active-membership migration refuses to run (without changing
data) if duplicates exist. team_cabinet adds no models.

## 5. Verification

Per change: `ruff check`, `ruff format --check`, `mypy --strict`, `import-linter`
(6 contracts kept), `makemigrations --check`, and the test suite in Docker against
PostgreSQL/Redis/RabbitMQ. OpenAPI schema (`spectacular --validate --fail-on-warn`)
clean. Full non-integration suite on `feature/p0-4-cabinet` (PostgreSQL/Redis/RabbitMQ
in Docker): **398 passed, 1 skipped, 7 deselected** (the skip is the repo-hygiene
check that only runs in CI; the 7 deselected are the integration tests). Integration
tests (Celery) were not run in this session; P0 adds no Celery tasks except the
payments outbox event.

## 6. Open questions / not finalised (documented, not invented)

- Traffic-light **yellow/green** rules and the **0-100 day score** — not approved;
  yellow is off by default, the proposal is configurable in `settings.TEAM_CABINET`.
- **Working-day calendar** (working Saturdays) — not built; plan-fact uses calendar
  days and every day counts as a working day for the traffic light.
- `leads/{id}/convert/` does not yet link to a pre-existing `customer_id` (it
  creates/reuses the lead's own customer).
- Existing companies' currency/timezone defaults (KGS / Asia/Bishkek) apply to new
  companies; migrating existing ones is an owner decision.

## 7. Blocked — need infrastructure / credentials (out of P0 code)

- Telegram delivery and the scheduled planerka snapshot push (10:05 / 17:30) — need a
  Telegram bot; the live board is available on demand via `team/today/`.
- Call recording/transcription + AI call review, АТС (Sipuni) — need recordings,
  consents, the АТС and GPU/LLM.
- Weekly/monthly AI reports, one-to-one, funnel analytics, hygiene, exports,
  13-month snapshots, automations, Amo import — P1/P2.

## 8. Local update steps

1. `git fetch origin`, check out `feature/p0-4-cabinet`.
2. `uv sync --all-groups`.
3. Apply migrations to the control plane and each tenant DB (through the stack):
   `docker compose exec api python manage.py migrate` (and per-tenant as the project
   does). All migrations are additive.
4. Run `make check` (lint/type/arch + non-integration tests) and the Docker test
   overlay for integration tests.

## 9. Notes for the frontend

- All new list endpoints return `{count, results}` with `limit`/`offset`; money is a
  decimal string; `*_at` is UTC, `*_date` is `YYYY-MM-DD`; errors keep the
  `{error:{code,message,details?,request_id}}` shape. `X-Tenant` header as before.
- `/me` now includes `product_role`, `permissions`, company and department per
  membership — drive UI gating off `permissions` where present.
- Deal transitions go through `POST sales/deals/{id}/stage/`; the stage's `kind`
  decides won/lost. Tasks use `due_group`/`is_overdue` from the backend (company tz).
- Cabinet endpoints need the `team_cabinet.view` permission; a manager gets 404/403.
