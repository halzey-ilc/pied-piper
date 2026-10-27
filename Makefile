# Convenience wrapper. Every target maps to uv / docker compose and has an
# equivalent in scripts/dev.sh for environments without Make.
.DEFAULT_GOAL := help
UV ?= uv
RUN := $(UV) run --no-sync
COMPOSE ?= docker compose

.PHONY: help install env up down logs ps rebuild \
        lint format typecheck arch security test test-int check \
        migrate makemigrations check-migrations seed shell rabbitmq-ui clean

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install the locked toolchain into .venv
	$(UV) sync --all-groups

env: ## Create .env with generated secrets (no-op if it exists)
	./scripts/init_env.sh

# --- docker compose ---------------------------------------------------------
up: ## Build and start the full stack (detached)
	$(COMPOSE) up -d --build

down: ## Stop the stack (keep volumes)
	$(COMPOSE) down

logs: ## Follow logs of all services
	$(COMPOSE) logs -f

ps: ## Show service status
	$(COMPOSE) ps

rebuild: ## Rebuild images without cache
	$(COMPOSE) build --no-cache

rabbitmq-ui: ## Forward the RabbitMQ management UI to http://localhost:15672
	$(COMPOSE) exec rabbitmq true && echo "Run: docker compose port rabbitmq 15672 (not published by default)"

# --- quality ----------------------------------------------------------------
lint: ## Ruff lint
	$(RUN) ruff check .

format: ## Ruff format + autofix
	$(RUN) ruff format . && $(RUN) ruff check --fix .

typecheck: ## mypy (strict)
	$(RUN) mypy

arch: ## import-linter architecture contracts
	$(RUN) lint-imports --no-cache

security: ## bandit + pip-audit
	$(RUN) bandit -q -c pyproject.toml -r config src && $(RUN) pip-audit

test: ## Unit/api/security/architecture tests (no external services)
	$(RUN) pytest -m "not integration"

test-int: ## Integration tests (requires the running stack)
	$(COMPOSE) -f compose.yaml -f compose.test.yaml run --rm tests

check: lint typecheck arch test security ## Run all static checks and non-integration tests

# --- django -----------------------------------------------------------------
migrate: ## Apply control-plane migrations (inside the api container)
	$(COMPOSE) exec api python manage.py migrate --noinput

makemigrations: ## Generate migrations (test settings, no DB needed)
	DJANGO_SETTINGS_MODULE=config.settings.test $(RUN) python manage.py makemigrations

check-migrations: ## Fail if models and migrations are out of sync
	DJANGO_SETTINGS_MODULE=config.settings.test $(RUN) python manage.py makemigrations --check --dry-run

seed: ## Provision a local demo tenant (needs TENANT_PROVISIONING_ENABLED=true)
	$(COMPOSE) exec -e TENANT_PROVISIONING_ENABLED=true api \
		python manage.py provision_tenant demo --name "Demo Company" --create-organization

shell: ## Django shell in the api container
	$(COMPOSE) exec api python manage.py shell

clean: ## Remove caches and build artifacts
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov dist build var
