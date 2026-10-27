"""Gunicorn configuration for the API container."""

import os
from typing import Any

bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
threads = 1  # tenant connection aliases are per-process; keep workers single-threaded
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
graceful_timeout = 20
keepalive = 5
max_requests = 2000
max_requests_jitter = 200
worker_tmp_dir = "/tmp"  # noqa: S108  # nosec B108 - gunicorn heartbeat dir; mount as tmpfs
accesslog = None  # request logging is done by the application (structured, redacted)
errorlog = "-"
limit_request_line = 4094
limit_request_fields = 100
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")

_multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
if _multiproc_dir:
    os.makedirs(_multiproc_dir, exist_ok=True)


def child_exit(server: Any, worker: Any) -> None:
    if _multiproc_dir:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)  # type: ignore[no-untyped-call]
