# Engineering decisions

This file records deviations from, and clarifications of, the fixed decisions in
`docs/spec/proof-papers-sol-implementation-spec.md` (section 2). Each entry gives
the evidence behind it.

## D001 — Repository root is the project root (T00, 2026-11-03)

Spec section 3 shows a `proof-papers/` directory. The workspace repository
(`psudorndm/proofs-hub`) was empty when work started: no commits, no planning
documents, no user files. Because the repository is dedicated to this project
and GitHub Actions only reads workflows from the repository root's
`.github/workflows/`, the `proof-papers/` tree from section 3 is laid out at the
repository root. Paths in the spec such as `proof-papers/pipeline/...` map to
`pipeline/...` here.

The specification itself is preserved unchanged at
`docs/spec/proof-papers-sol-implementation-spec.md`
(SHA-256 `6c8c4536c09c9649c14e22315a301ce921f9569f0cc8d6d6cf11e47997b5ce05`).

## D002 — Runtime baselines selected (T00, 2026-11-03)

| Concern | Spec baseline | Selected for this build | Evidence |
|---|---|---|---|
| Python | 3.12 | CPython 3.12.13 (uv-managed) | `uv python install 3.12.13` succeeded; system also has 3.12.3 and 3.13.18 |
| Python packaging | uv | uv 0.11.32 | `uv --version` |
| Node | 22 | Node v22.23.4 | `node --version` |
| npm | npm + lockfile | npm 10.9.9 | `npm --version` |
| PostgreSQL | 17 | `postgres:17` Docker image, server 17.11 | `docker run --rm postgres:17 postgres --version` |

Python 3.12 is pinned with `requires-python = ">=3.12,<3.13"` so that the lock
resolves for the baseline. The host's PostgreSQL 16 client (`psql` 16.15) is only
a convenience client; all server-side tests run against the PostgreSQL 17
container. `pg_dump` used by `backup` must match the server major version, so
backup/restore runs `pg_dump`/`pg_restore` from the PostgreSQL 17 container image
(decided in T15 if this changes).

## D003 — Docker daemon must be started manually in this sandbox (T00)

The Docker CLI and Compose plugin are installed, but the daemon was not running.
`dockerd` was started in the background for this session. This is a property of
the sandbox, not of the project; developer machines and GitHub Actions run their
own daemon/service containers.
