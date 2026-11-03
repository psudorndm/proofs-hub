"""Validate an exported release before any build or deployment uses it."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lxml import etree
from pydantic import ValidationError

from proof_index.errors import DataValidationError
from proof_index.hashing import sha256_hex
from proof_index.models.public import ListPage, Manifest, PublicPaper

FORBIDDEN_KEYS = frozenset(
    {
        "password",
        "database_url",
        "headers",
        "error_summary",
        "traceback",
        "reviewer_label",
        "reviewer_contact",
        "aws_secret_access_key",
        "authorization",
    }
)
FORBIDDEN_TEXT = (
    re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s\"]*:[^\s\"]*@"),
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _walk_keys(value: Any, path: str, problems: list[str]) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                problems.append(f"{path}: forbidden key {key!r}")
            _walk_keys(inner, f"{path}.{key}", problems)
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            _walk_keys(inner, f"{path}[{i}]", problems)


def validate_release(root: Path, *, require_live: bool = False) -> dict[str, Any]:
    problems: list[str] = []
    if not (root / "manifest.json").is_file():
        raise DataValidationError(f"{root} has no manifest.json")
    try:
        manifest = Manifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
    except ValidationError as exc:
        raise DataValidationError(f"invalid manifest: {exc}") from None
    release_id = manifest.release_id
    if root.name != release_id:
        problems.append(f"directory name {root.name!r} differs from release_id {release_id!r}")

    if require_live:
        if manifest.dataset_mode != "live":
            problems.append("live deployment guard: release is a fixture dataset")
        if manifest.code_revision == "unknown-local":
            problems.append("live deployment guard: production release needs a real code revision")

    # Checksums cover every other file.
    checksums = json.loads((root / "checksums.json").read_text(encoding="utf-8"))
    listed = checksums.get("files", {})
    actual = {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p.name != "checksums.json"
    }
    for missing in sorted(set(listed) - actual):
        problems.append(f"checksummed file missing: {missing}")
    for extra in sorted(actual - set(listed)):
        problems.append(f"file not covered by checksums: {extra}")
    for name in sorted(set(listed) & actual):
        if sha256_hex((root / name).read_bytes()) != listed[name]:
            problems.append(f"checksum mismatch: {name}")

    # Every text file: release ID consistency and secret/trace scan.
    for name in sorted(actual | {"checksums.json"}):
        text = (root / name).read_text(encoding="utf-8")
        for pattern in FORBIDDEN_TEXT:
            if pattern.search(text):
                problems.append(f"{name}: forbidden content ({pattern.pattern[:30]}...)")
        if name.endswith(".json"):
            data = json.loads(text)
            _walk_keys(data, name, problems)
            if isinstance(data, dict) and "release_id" in data and data["release_id"] != release_id:
                problems.append(f"{name}: release_id {data['release_id']!r} != {release_id!r}")

    # Papers.
    paper_ids: set[str] = set()
    for path in sorted((root / "papers").glob("*.json")):
        try:
            paper = PublicPaper.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            problems.append(f"{path.name}: invalid paper ({str(exc)[:200]})")
            continue
        if path.stem != paper.id:
            problems.append(f"{path.name}: id {paper.id} does not match filename")
        if not (root / "citations" / f"{paper.id}.bib").is_file():
            problems.append(f"{paper.id}: missing citation file")
        paper_ids.add(paper.id)
    if len(paper_ids) != manifest.included_work_count:
        problems.append(
            f"manifest counts {manifest.included_work_count} works, found {len(paper_ids)}"
        )

    # Lists: canonical works once, totals consistent, references resolvable.
    list_dirs = sorted(
        {p.parent for p in root.glob("lists/**/*.json")}
        | {p.parent for p in root.glob("topics/*/*.json")}
    )
    list_count = 0
    for directory in list_dirs:
        pages = sorted(directory.glob("*.json"), key=lambda p: int(p.stem))
        seen: set[str] = set()
        totals = set()
        for path in pages:
            try:
                page = ListPage.model_validate_json(path.read_text(encoding="utf-8"))
            except ValidationError as exc:
                problems.append(f"{path.relative_to(root)}: invalid list page ({str(exc)[:200]})")
                continue
            totals.add((page.total, page.page_count))
            for item in page.items:
                if item.id in seen:
                    problems.append(f"{path.relative_to(root)}: {item.id} listed twice")
                if item.id not in paper_ids:
                    problems.append(f"{path.relative_to(root)}: unknown paper {item.id}")
                seen.add(item.id)
        if len(totals) != 1:
            problems.append(f"{directory.relative_to(root)}: inconsistent totals {sorted(totals)}")
        elif totals:
            total, page_count = totals.pop()
            if total != len(seen) or page_count != len(pages):
                problems.append(
                    f"{directory.relative_to(root)}: total {total} vs {len(seen)} items"
                )
        list_count += 1
    for topic, count in manifest.topic_counts.items():
        first = root / "topics" / topic / "1.json"
        if not first.is_file() or json.loads(first.read_text())["total"] != count:
            problems.append(f"topic {topic}: manifest count {count} disagrees with list")

    # Feeds.
    for feed in ("new", "updated"):
        path = root / "feeds" / f"{feed}.atom"
        try:
            tree = etree.fromstring(
                path.read_bytes(), etree.XMLParser(resolve_entities=False, no_network=True)
            )
        except (OSError, etree.XMLSyntaxError) as exc:
            problems.append(f"feed {feed}: unreadable ({exc})")
            continue
        ids = [e.findtext(f"{ATOM_NS}id") for e in tree.findall(f"{ATOM_NS}entry")]
        if len(ids) > 100:
            problems.append(f"feed {feed}: more than 100 entries")
        if len(ids) != len(set(ids)):
            problems.append(f"feed {feed}: duplicate entry ids")

    # Aliases resolve to live exported works.
    aliases = json.loads((root / "aliases.json").read_text())["aliases"]
    for old, new in aliases.items():
        if new not in paper_ids:
            problems.append(f"alias {old} -> {new} does not resolve to an exported paper")

    schema = json.loads((root / "schema" / "public-paper.schema.json").read_text())
    if schema != PublicPaper.model_json_schema():
        problems.append("schema/public-paper.schema.json does not match the public model")

    report = {
        "release_id": release_id,
        "dataset_mode": manifest.dataset_mode,
        "papers": len(paper_ids),
        "lists": list_count,
        "problems": problems,
        "ok": not problems,
    }
    if problems:
        raise DataValidationError(
            f"release {release_id} failed validation ({len(problems)} problems): "
            + "; ".join(problems[:10])
        )
    return report
