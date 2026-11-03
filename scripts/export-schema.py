"""Write the public JSON Schemas generated from the Python models.

uv run python scripts/export-schema.py [--out apps/web/src/lib/schema]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from proof_index.models.public import ListPage, Manifest, PaperCard, PublicPaper, RecentLists

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "apps/web/src/lib/schema")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, model in (
        ("public-paper", PublicPaper),
        ("paper-card", PaperCard),
        ("list-page", ListPage),
        ("recent-lists", RecentLists),
        ("manifest", Manifest),
    ):
        schema = model.model_json_schema(by_alias=True)
        (args.out / f"{name}.schema.json").write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n"
        )
        print(f"wrote {args.out / (name + '.schema.json')}")


if __name__ == "__main__":
    main()
