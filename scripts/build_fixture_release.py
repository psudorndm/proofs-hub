"""Build a clearly labeled *fixture* release from the synthetic cases.

Creates a disposable database named ``proofs_fixture_*`` on the server named by
``FIXTURE_DATABASE_ADMIN_URL`` (default: ``TEST_DATABASE_ADMIN_URL``), migrates it,
loads the synthetic cases through the real pipeline, exports with
``dataset_mode=fixture``, validates, and drops the database. It never touches
``DATABASE_URL``.

    uv run python scripts/build_fixture_release.py --out work/releases/fixture-preview
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from proof_index.clock import FixedClock
from proof_index.config.site import load_site
from proof_index.config.sources import load_sources
from proof_index.db.repository import make_engine, migrate
from proof_index.models.classification import load_taxonomy
from proof_index.publish.export import export_release
from proof_index.publish.fixture import build_fixture_catalog
from proof_index.publish.validate import validate_release
from proof_index.storage.local import LocalBlobStore

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base-path", default=os.environ.get("PUBLIC_BASE_PATH", "/"))
    parser.add_argument(
        "--site-url", default=os.environ.get("PUBLIC_SITE_URL", "http://localhost:4321")
    )
    parser.add_argument("--bulk", type=int, default=0, help="Add N generated synthetic records.")
    args = parser.parse_args()
    admin_url = os.environ.get("FIXTURE_DATABASE_ADMIN_URL") or os.environ.get(
        "TEST_DATABASE_ADMIN_URL"
    )
    if not admin_url:
        print("set FIXTURE_DATABASE_ADMIN_URL or TEST_DATABASE_ADMIN_URL", file=sys.stderr)
        return 2
    name = f"proofs_fixture_{uuid.uuid4().hex[:10]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        migrate(url)
        engine = make_engine(url)
        clock = FixedClock(datetime(2026, 11, 3, 12, tzinfo=UTC))
        taxonomy = load_taxonomy(ROOT / "config/taxonomy.yaml")
        work = args.out.parent / f".fixture-work-{name}"
        result = build_fixture_catalog(
            engine,
            LocalBlobStore(work / "blobs"),
            load_sources(ROOT / "config/sources.yaml"),
            clock,
            cases_path=ROOT / "tests/fixtures/synthetic/cases.json",
            taxonomy=taxonomy,
            rules_path=ROOT / "config/relevance-rules.yaml",
            venues_path=ROOT / "config/venues.yaml",
            work_dir=work,
            bulk=args.bulk,
        )
        exported = export_release(
            engine,
            args.out,
            clock=clock,
            site=load_site(ROOT / "config/site.yaml"),
            sources=load_sources(ROOT / "config/sources.yaml"),
            taxonomy=taxonomy,
            rules_version="rules-v1",
            public_site_url=args.site_url,
            base_path=args.base_path,
            dataset_mode="fixture",
        )
        report = validate_release(args.out)
        engine.dispose()
        print(
            json.dumps(
                {
                    "release": exported.release_id,
                    "path": str(args.out),
                    "papers": report["papers"],
                    "runs": [r["state"] for r in result["runs"]],
                },
                indent=2,
            )
        )
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
