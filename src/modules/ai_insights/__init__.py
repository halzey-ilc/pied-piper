"""AI insights: CRM-side integration with the separate CRM AI Platform.

The CRM stays the source of truth. A user asks for an insight (e.g. a call
summary); the CRM checks their rights, sends the allowed text to the AI
Platform as a job with a short-lived token bound to the active tenant, polls
for the result in a worker and stores the structured output here. The AI
Platform holds no credentials for the CRM and never writes to it.
"""
