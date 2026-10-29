# AI integration (`modules.ai_insights`)

The CRM uses a **separate** service, the CRM AI Platform (`../crm-ai-platform`),
for AI analysis. The CRM remains the source of truth and the only place where
user rights are checked. AI features are **off** unless `AI_PLATFORM_ENABLED=true`.

## Flow: the CRM pulls, the AI Platform never writes

```
POST /api/v1/ai/calls/<call_id>/summary/      (user JWT + X-Tenant)
  1. telephony visibility check (CallService.get)  -> 404 if the user cannot see the call
  2. RBAC ai.request on the call (own/team/all)     -> 403 (viewer has no ai.request)
  3. CallInsight row (pending), audit "call_insight.requested", 202
worker  integrations.ai_submit_insight
  4. re-reads the call text; if it changed since the request -> failed/source_changed
  5. POST {AI}/v1/jobs  with a 5-minute HS256 token: tenant = active tenant, sub = user:<id>
     Idempotency-Key = crm-insight-<insight id>  (a redelivered task gets the same AI job)
worker  integrations.ai_poll_insight   (2s, then 5s, then 15s; gives up after 15 min)
  6. GET  {AI}/v1/jobs/<id>; on success stores the structured result, emits
     ai_insights.call_insight_completed, audits "call_insight.succeeded"
GET  /api/v1/ai/calls/<call_id>/summary/      -> latest insight of a call the user can see
```

Why pull instead of a callback: the AI Platform holds **no CRM credentials**
and the CRM exposes **no new inbound endpoint** for a machine identity.

## Operations digests (AI Operations)

The rules live in the CRM (`modules.operations`, no AI): forgotten leads,
deals without a next step, overdue tasks and stalled deals, with per-company
thresholds (`GET /api/v1/operations/findings/`, `GET/PUT /api/v1/operations/settings/`).
The AI Platform only **explains** what the rules found:

```
POST /api/v1/ai/operations/digest/  {"language": "ru"|"ky"|"en"}   (ai.request; viewer 403)
  snapshot of the caller's own findings (their visibility), as
  {"ref": "lead:<uuid>", "kind": "stale_lead", "age_days": 9} + counts + thresholds
  -> stored, then integrations.ai_submit_digest / ai_poll_digest (job operations.digest.v1)
GET  /api/v1/ai/operations/digest/   -> the caller's own latest digest (never another user's)
beat 06:00 integrations.ai_daily_digests -> one daily digest per member with ai.request
     who has findings (unique per user and day; no-op while AI is off)
```

Only references, kinds, ages and thresholds are sent; names, e-mails, amounts
and text never leave the CRM. The AI output cites `finding_refs`; the AI
Platform rejects outputs that cite anything not in the input.

## KPI explanations (AI Business Analyst)

KPIs are computed by `modules.sales_kpi` (SQL aggregation over the metric read
model). For an explanation the CRM also computes, in `domain/kpi_payload.py`:
- the period before;
- each metric's change, and its relative change in %;
- ratios turned into percentages, whose change is in percentage points.

The AI receives only these numbers and may not introduce any other: the AI
Platform rejects every text containing a number that is not in the input.

```
POST /api/v1/ai/analytics/kpi-explanation/ {"period": "month"|"week"|"day", "date"?, "language"?}
     (reports.view + ai.request: owner, admin, manager; not sales_rep, not viewer)
GET  /api/v1/ai/analytics/kpi-explanation/ -> the caller's own latest explanation
```

Operations digests and KPI explanations are both **AI reports**:
- one table `ai_insights_report` with a `kind` column;
- one worker pair `integrations.ai_submit_report` / `ai_poll_report`;
- the response always includes `input`, the exact numbers sent, so every statement can be
  traced back to them.

## Reply suggestions (AI Communication Assistant)

```
POST /api/v1/ai/conversations/<id>/reply/ {"language"?}  -> 202, or 200 for an unchanged conversation
GET  /api/v1/ai/conversations/<id>/reply/               -> the latest suggestion
     (conversation visibility + ai.request on it; a sales_rep assigns an unassigned
      conversation first; 400 when the company wrote last)
```

The worker sends the latest 20 messages (direction and text) of the conversation
as `message.reply.v1`. The contact's phone number, username and name are **not**
sent. The result has `intent`, `urgency`, `evidence` (a verbatim quote of a
customer message), `draft_reply` and `needs_human`.

- **Nothing is sent to the customer.** `auto_send` is always `false`; a person
  sends their own message through `POST .../communications/conversations/<id>/messages/`.
- The AI Platform rejects drafts containing numbers, links or e-mails that are not in
  the conversation: prices, dates and terms come from people, not from the model.
- Complaints and cancellations always have `needs_human: true`.
- The suggestion row stores the input's sha256, not the message texts. If a message
  arrives between the request and the worker, the suggestion fails `source_changed`
  instead of answering an outdated conversation.

## Notifications

Every finished AI result emits an event through the outbox:
- `ai_insights.report_completed`;
- `ai_insights.call_insight_completed`;
- `ai_insights.reply_suggestion_completed`.

`modules.notifications` turns each event into an in-app notification for the person who
asked, or the person a daily digest is for:
- category `ai`, which users can mute;
- a link to the result;
- a "failed" title when no result came back.

The events carry ids only, never texts or results.

## Call summaries: what is verified

`result.key_points` and `result.next_step` carry verbatim quotes from the call
and are checked. `result.summary` is free text and is **not** checked, so the API
marks it `summary_grounded: false`. On a local 4B model, Kyrgyz summaries
contained factual errors (see `crm-ai-platform/docs/MODEL_EVALUATION.md`).

## Data and security

| Concern | How |
|---|---|
| Tenant | Token claim from `require_tenant()`, never from the request; results stored in that tenant's database |
| Rights | Telephony's own visibility check + `ai.request` (owner/admin ALL, manager TEAM, sales_rep OWN, viewer none) |
| What is sent | Call `notes` only (no transcripts exist yet), with the call id as an opaque reference |
| What is stored | Structured output (`call_summary.output.v1`), provider, model, `is_mock`; the text's sha256, not the text |
| Mock results | `is_mock=true` is shown to clients; unknown provenance is treated as mock |
| Key | `AI_PLATFORM_TOKEN_KEY` ≥ 32 chars, must differ from `JWT_SIGNING_KEY`/`DJANGO_SECRET_KEY`; `_FILE` supported |
| Failures | Network/5xx/429 → `TransientError` (Celery retry, then DLQ); 4xx/contract mismatch → insight `failed` |
| Queue | Existing `integrations` queue (outbound calls), no topology change |

Tests: `tests/unit/test_ai_insights.py`, `tests/api/test_ai_insights.py`,
`tests/security/test_ai_insights_isolation.py`, `tests/unit/test_operations_rules.py`,
`tests/api/test_operations.py`, `tests/api/test_ai_operations_digest.py`,
`tests/unit/test_kpi_payload.py`, `tests/api/test_ai_kpi_explanation.py`,
`tests/api/test_ai_reply_suggestions.py`.

## Running locally with the AI Platform

```bash
(cd ../crm-ai-platform && ./scripts/e2e_with_crm.sh && ./scripts/e2e_operations.sh && ./scripts/e2e_kpi.sh && ./scripts/e2e_reply.sh)
```

The script reconnects `api` and `worker` through `compose.ai.yaml`, which joins
them to the `crm-ai-link` network. That network contains only the AI gateway,
so the CRM cannot reach the AI Platform's database and service names do not
collide. Without the overlay the CRM runs as before with AI disabled.

## Not done yet

- Transcripts / audio (summaries use call notes), language detection.
- Per-company script-evaluation rules.
- Reply suggestions: product facts (price list, availability) as CRM data the
  draft may cite; measuring how often drafts are used; a real-model evaluation.
- E-mail/push delivery of AI notifications (in-app only for now).
- Stalled deals use `updated_at` because there is no stage history.
- Showing insights in the call card of a frontend (there is no frontend).
