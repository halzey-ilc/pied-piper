# Local development

## Prerequisites

- WSL2 Ubuntu (or Linux/macOS), Docker + Docker Compose.
- [uv](https://docs.astral.sh/uv/) ≥ 0.13 on `PATH`
  (`curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" sh`).
- GNU Make optional (`scripts/dev.sh` mirrors every target).

## First run

```bash
make install           # uv sync --all-groups  -> .venv with Python 3.13
make env               # writes .env with generated secrets (never overwrites)
make up                # build + start postgres, redis, rabbitmq, api, worker, beat
make ps                # all services healthy?
curl -s localhost:8000/health/ready
```

The `api` container applies control-plane migrations on start
(`RUN_MIGRATIONS_ON_START=1`). To run them manually: `make migrate`.

## Create a demo tenant

Provisioning is off by default. The `seed` target enables it for one command:

```bash
make seed              # creates org "demo" + its tenant database, migrates it
```

Then create a user and membership via `make shell` (Django shell), obtain a
token and call the API:

```bash
curl -s -X POST localhost:8000/api/v1/auth/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"<user>","password":"<password>"}'

curl -s localhost:8000/api/v1/customers/ \
  -H "Authorization: Bearer <access>" -H "X-Tenant: demo"
```

API docs (local/test only): http://localhost:8000/api/docs/

## Tests & quality (host, in the venv)

```bash
make test          # unit + api + security + architecture (spins up throwaway test DBs)
make lint          # ruff
make typecheck     # mypy strict
make arch          # import-linter
make security      # bandit + pip-audit
make check         # all of the above
```

The DB-backed tests (`@pytest.mark.django_db`) need PostgreSQL reachable with a
CREATEDB-capable `crm_app` role. With the stack up, point the host env at it:

```bash
DB_HOST=127.0.0.1 DB_PASSWORD=<from .env> RABBITMQ_PASSWORD=<from .env> \
  make test
```

## Integration tests (full stack)

```bash
make test-int      # docker compose runs pytest -m integration against the live services
```

These **fail** (never skip) if a service is missing.

## Useful

```bash
make logs          # follow all logs
make down          # stop (keep data volumes)
make check-migrations   # fail if models and migrations drift
docker compose port rabbitmq 15672   # expose the management UI ad hoc
```

## Troubleshooting

- *Config/startup check failed* on container start → a required/unsafe env var;
  the message names the variable. Re-run `make env` or fix `.env`.
- *connection refused :5432* in host tests → the stack isn't up, or `DB_HOST`
  isn't `127.0.0.1`.
- Port 8000 busy → change the published port in `compose.yaml` (`api.ports`).
