# Thin wrappers around documented commands. Each target runs a real command.
.PHONY: setup db-up db-down doctor migrate test test-db fixture-export schema web-build web-preview web-test web-browser-check lint typecheck check

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

# Static site from one validated release: make web-build CATALOG_DIR=work/releases/<id>
CATALOG_DIR ?= $(CURDIR)/$(FIXTURE_RELEASE)
web-build:
	cd apps/web && npm ci && CATALOG_DIR=$(abspath $(CATALOG_DIR)) npm run build

web-preview: web-build
	cd apps/web && npx astro preview --host 127.0.0.1

web-test:
	cd apps/web && CATALOG_DIR=$(abspath $(CATALOG_DIR)) npm run check && CATALOG_DIR=$(abspath $(CATALOG_DIR)) npm test

# Real-index browser checks (Playwright Chromium) against a release with >25 papers.
SEARCH_RELEASE ?= work/releases/fixture-search
web-browser-check:
	rm -rf $(SEARCH_RELEASE)
	TEST_DATABASE_ADMIN_URL=$(TEST_DATABASE_ADMIN_URL) $(UV) run python scripts/build_fixture_release.py --out $(SEARCH_RELEASE) --bulk 40
	cd apps/web && CATALOG_DIR=$(abspath $(SEARCH_RELEASE)) npm run build
	cd apps/web && (npx astro preview --port 4321 --host 127.0.0.1 &) && sleep 5 && \
	  node tests/browser/home-checks.mjs http://127.0.0.1:4321/; status=$$?; npx astro preview stop; exit $$status

lint:
	$(UV) run ruff check pipeline tests scripts db
	$(UV) run ruff format --check pipeline tests scripts db

typecheck:
	$(UV) run mypy

check: lint typecheck test
