# Reporting

**Status:** implemented (tenant plane).

Asynchronous report exports, rendered off the request path.

## Flow

1. `POST /api/v1/analytics/reports/` validates the request, creates a
   `ReportJob` (status `queued`) and returns it immediately (202). Idempotent on
   `idempotency_key`: repeating a request returns the same job (200). Enqueues
   `reports.process_report` **after commit**.
2. The worker renders the report (`reports` queue), writes the file to export
   storage and sets the job to `succeeded` (or `failed`).
3. `GET /api/v1/analytics/reports/{id}/` returns the job status; when
   `succeeded`, `GET .../download/` streams the file.

Heavy rendering never runs in an API request.

## Report types

- `kpi_summary` — a CSV of KPI values over the requested period, rendered from
  the sales_kpi read model.

## Storage

`FilesystemExportStorage` writes under a single base directory (a Docker volume
shared by the API and worker in the stack). Keys are namespaced by tenant and
validated against path traversal.

## Permissions

`reports.generate` to request a report; `reports.view` to read status and
download (both company-wide; sales reps excluded).
