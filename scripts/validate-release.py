"""Validate a release directory (wrapper around ``proof-index release validate``).

    uv run python scripts/validate-release.py work/releases/<id> [--require-live]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from proof_index.errors import ProofIndexError
from proof_index.publish.validate import validate_release


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args()
    try:
        report = validate_release(args.release, require_live=args.require_live)
    except ProofIndexError as exc:
        print(f"error[{exc.code}]: {exc}", file=sys.stderr)
        return exc.exit_code
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
