"""Django settings are valid (acceptance item 1)."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import call_command

ROOT = Path(__file__).resolve().parents[2]


def test_system_check_passes_for_test_settings() -> None:
    call_command("check", fail_level="WARNING")


@pytest.mark.parametrize("settings_module", ["config.settings.production"])
def test_deploy_check_passes_for_production_settings(settings_module: str) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": f"{ROOT}{os.pathsep}{ROOT / 'src'}",
        "DJANGO_SETTINGS_MODULE": settings_module,
        "DJANGO_ENV": "production",
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "JWT_SIGNING_KEY": secrets.token_urlsafe(64),
        "DJANGO_ALLOWED_HOSTS": "api.example.com",
        "DB_PASSWORD": secrets.token_urlsafe(24),
        "DB_SSLMODE": "verify-full",
        "REDIS_PASSWORD": secrets.token_urlsafe(24),
        "RABBITMQ_PASSWORD": secrets.token_urlsafe(24),
        "METRICS_TOKEN": secrets.token_urlsafe(24),
    }
    result = subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for value in (env["DJANGO_SECRET_KEY"], env["DB_PASSWORD"]):
        assert value not in result.stdout + result.stderr


def test_production_settings_refuse_placeholder_secrets() -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": f"{ROOT}{os.pathsep}{ROOT / 'src'}",
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "DJANGO_ENV": "production",
        "DJANGO_SECRET_KEY": "__GENERATE__",
        "JWT_SIGNING_KEY": "__GENERATE__",
        "DB_PASSWORD": "__GENERATE__",
        "RABBITMQ_PASSWORD": "__GENERATE__",
    }
    result = subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode != 0
    assert "Unsafe environment" in result.stderr
