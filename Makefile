# Thin wrappers around documented commands. Each target runs a real command.
.PHONY: setup db-up db-down doctor migrate test test-db fixture-export schema lint typecheck check

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

migrate:
	$(UV) run alembic -c db/alembic.ini upgrade head

test:
	$(UV) run pytest -m "not db"

# Creates/drops disposable proofs_test_* databases on the local Compose server.
TEST_DATABASE_ADMIN_URL ?= postgresql+psycopg://proofs:proofs@127.0.0.1:5432/postgres
test-db:
	TEST_DATABASE_ADMIN_URL=$(TEST_DATABASE_ADMIN_URL) $(UV) run pytest

# Synthetic, clearly labeled preview release (never deployable as live data).
FIXTURE_RELEASE ?= work/releases/fixture-preview
fixture-export:
	rm -rf $(FIXTURE_RELEASE)
	TEST_DATABASE_ADMIN_URL=$(TEST_DATABASE_ADMIN_URL) $(UV) run python scripts/build_fixture_release.py --out $(FIXTURE_RELEASE)

schema:
	$(UV) run python scripts/export-schema.py

lint:
	$(UV) run ruff check pipeline tests scripts db
	$(UV) run ruff format --check pipeline tests scripts db

typecheck:
	$(UV) run mypy

check: lint typecheck test
