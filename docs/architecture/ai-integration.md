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
`tests/security/test_ai_insights_isolation.py`.

## Running locally with the AI Platform

```bash
(cd ../crm-ai-platform && make up && ./scripts/e2e_with_crm.sh)
```

The script reconnects `api` and `worker` through `compose.ai.yaml`, which joins
them to the `crm-ai-link` network. That network contains only the AI gateway,
so the CRM cannot reach the AI Platform's database and service names do not
collide. Without the overlay the CRM runs as before with AI disabled.

## Not done yet

- Transcripts / audio (summaries use call notes), language detection.
- Per-company script-evaluation rules, other insight kinds (operations, KPI explanations).
- Showing insights in the call card of a frontend (there is no frontend).
