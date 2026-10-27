"""Celery application. Tasks are thin adapters in entrypoints.workers."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

_multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
if _multiproc_dir:
    os.makedirs(_multiproc_dir, exist_ok=True)

app = Celery("crm")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.include = [
    "entrypoints.workers.tasks.diagnostics",
    "entrypoints.workers.tasks.outbox",
    "entrypoints.workers.tasks.events",
    "entrypoints.workers.tasks.integrations",
    "entrypoints.workers.tasks.reports",
]

from platform_core.infrastructure.observability.celery_signals import (  # noqa: E402
    connect_celery_signals,
)

connect_celery_signals(app)
