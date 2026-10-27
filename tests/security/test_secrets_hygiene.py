"""Repository-level secret hygiene."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_env_file_is_ignored_by_git_and_docker() -> None:
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".env" in gitignore
    assert ".env" in dockerignore


def test_env_example_contains_only_placeholders_for_secrets() -> None:
    secret_keys = re.compile(r"(SECRET|PASSWORD|TOKEN|SIGNING_KEY|DSN)", re.I)
    for line in (ROOT / ".env.example").read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if secret_keys.search(key):
            assert value in ("", "__GENERATE__"), f"{key} must be empty or __GENERATE__"
