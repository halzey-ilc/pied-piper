#!/usr/bin/env bash
# Thin task runner so the project works without GNU Make.
# Usage: ./scripts/dev.sh <task>   (see `./scripts/dev.sh help`)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"

uvx() { command uv run --no-sync "$@"; }

case "${1:-help}" in
  install)    uv sync --all-groups ;;
  env)        ./scripts/init_env.sh ;;
  lint)       uvx ruff check . ;;
  format)     uvx ruff format . && uvx ruff check --fix . ;;
  typecheck)  uvx mypy ;;
  arch)       uvx lint-imports --no-cache ;;
  security)   uvx bandit -q -c pyproject.toml -r config src && uvx pip-audit ;;
  test)       uvx pytest -m "not integration" "${@:2}" ;;
  test-int)   uvx pytest -m integration "${@:2}" ;;
  test-all)   uvx pytest "${@:2}" ;;
  check)      "$0" lint && "$0" typecheck && "$0" arch && "$0" test && "$0" security ;;
  migrate)    uvx python manage.py migrate "${@:2}" ;;
  makemigrations) uvx python manage.py makemigrations "${@:2}" ;;
  check-migrations) DJANGO_SETTINGS_MODULE=config.settings.test uvx python manage.py makemigrations --check --dry-run ;;
  help|*)     grep -oE '^  [a-z-]+\)' "$0" | tr -d ' )' | sort | paste -sd' ' - ;;
esac
