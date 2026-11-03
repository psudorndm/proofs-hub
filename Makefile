# Thin wrappers around documented commands. Each target runs a real command.
.PHONY: setup db-up db-down db-wait doctor test lint typecheck check

UV ?= uv
COMPOSE ?= docker compose

setup:
	$(UV) sync --locked

db-up:
	$(COMPOSE) up -d --wait postgres

db-down:
	$(COMPOSE) down

doctor:
	$(UV) run proof-index doctor --check-db

test:
	$(UV) run pytest -m "not db"

lint:
	$(UV) run ruff check pipeline tests scripts
	$(UV) run ruff format --check pipeline tests scripts

typecheck:
	$(UV) run mypy

check: lint typecheck test
