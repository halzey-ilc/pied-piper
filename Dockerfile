# syntax=docker/dockerfile:1.7
# Multi-stage build. Dependencies are resolved from the committed uv.lock, so
# the image is reproducible. The final image runs as a non-root user and
# contains no build toolchain.

ARG PYTHON_VERSION=3.13

# --- builder --------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.13-python${PYTHON_VERSION}-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install only third-party dependencies first (cached unless the lock changes).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --locked --no-install-project --no-dev

# Then the project itself.
COPY pyproject.toml uv.lock README.md ./
COPY config ./config
COPY src ./src
COPY manage.py ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# --- runtime --------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src:/app" \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus

# libpq5 is the only runtime system dependency (psycopg binary wheels bundle the rest).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app config ./config
COPY --chown=app:app src ./src
COPY --chown=app:app manage.py ./
COPY --chown=app:app infrastructure/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /tmp/prometheus && chown app:app /tmp/prometheus

USER app
EXPOSE 8000

ENTRYPOINT ["entrypoint.sh"]
# Default: the API. Overridden by the worker/beat services in compose.yaml.
CMD ["gunicorn", "config.wsgi:application", "-c", "config/gunicorn.conf.py"]
