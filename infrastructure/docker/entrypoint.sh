#!/usr/bin/env bash
# Container entrypoint. Validates configuration early (fail fast) and, for the
# API role only, waits for the database and applies control-plane migrations
# when RUN_MIGRATIONS_ON_START=1 (local convenience; disabled in production).
set -euo pipefail

# Fail immediately with a clear message if the environment is invalid/unsafe.
python -c "import django; django.setup()" >/dev/null 2>&1 || {
    echo "Configuration/startup check failed:" >&2
    python -c "import django; django.setup()"
    exit 1
}

if [ "${RUN_MIGRATIONS_ON_START:-0}" = "1" ]; then
    echo "Applying control-plane migrations..."
    python manage.py migrate --noinput
fi

exec "$@"
