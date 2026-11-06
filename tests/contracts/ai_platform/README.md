Copies of the JSON Schemas published by the CRM AI Platform
(`halzey-ilc/crm-ai-platform`, commit `a4acd887`, folders `contracts/api` and
`contracts/model-schemas`). They are data for `tests/unit/test_ai_platform_contracts.py`:
the inputs are closed (`additionalProperties: false`), so what the CRM sends must match
them exactly or the platform rejects the job. Refresh them when the CRM moves to a new
job-type version; a published schema never changes.
