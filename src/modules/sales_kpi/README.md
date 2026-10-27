# Sales KPI

**Status:** implemented (tenant plane).

An event-driven read model and the KPIs computed from it.

## Read model

Domain events already emitted by other modules (deals, leads, activities) are
consumed by handlers in `infrastructure/handlers.py` and turned into
**metric facts** (`MetricFact`): one row per observation, e.g. a won deal
produces `deal_won=1`, `deal_closed=1` and `revenue_won=<amount>`. Recording is
idempotent (unique `source_event_id` + metric), and the facts are written in
the same transaction as the event's inbox row.

## KPIs

The catalog (`application/catalog.py`) defines KPIs as aggregations over metrics:
deals created/won, revenue won, win rate, leads captured, lead conversion rate,
activities logged, tasks completed. Values are computed **in the database**
(`DjangoMetricAggregator`) over a day/week/month window — no raw facts are loaded
into memory, so the endpoint stays light.

## Snapshots

`KpiSnapshotRecord` stores computed values per period for history/trends. The
`reports.compute_daily_kpis` Celery Beat task snapshots every active tenant's
day idempotently.

## API

- `GET /api/v1/analytics/kpis/?period=month&start=YYYY-MM-DD` — current values.
- `GET /api/v1/analytics/kpis/history/?period=&start=&end=` — stored snapshots.

Both require the `reports.view` permission (company-wide; sales reps excluded).
