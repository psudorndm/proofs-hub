# Cryptographic Proofs Research Index

A public, account-free research index for cryptographic proof systems, security
proofs, formal verification, and cryptographic protocol analysis, with full
historical coverage and daily updates.

**Status: under construction.** No part of the application is complete yet. See
[`docs/implementation-status.md`](docs/implementation-status.md) for the current
task, evidence, and blockers.

- Specification: [`docs/spec/proof-papers-sol-implementation-spec.md`](docs/spec/proof-papers-sol-implementation-spec.md)
- Decisions and deviations: [`docs/decisions.md`](docs/decisions.md)
- Verification log: [`docs/verification-report.md`](docs/verification-report.md)

## Local setup

Requirements: [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv from
`.python-version`), Docker with Compose, and Node 22 for the web app.

```bash
cp .env.example .env
```

```bash
make setup
```

```bash
make db-up
```

```bash
uv run proof-index doctor --check-db
```

`make test` runs the unit tests; `make test-db` runs everything against disposable
PostgreSQL databases on the Compose server.

## Preview the reader site

```bash
make fixture-export
```

```bash
make web-preview
```

The fixture release is built from synthetic test cases and every page shows
“Preview dataset — not the live research index.” To build from a real export, pass
`CATALOG_DIR=work/releases/<release-id>` to `make web-build`.
