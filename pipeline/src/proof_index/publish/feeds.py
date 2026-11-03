"""Atom feeds for new publications and updates (spec section 11.3).

Entry IDs are stable tag URIs derived from the work ID (new) or the event key
(updated), never from titles. Each feed carries at most 100 entries. All text is
escaped by the XML serializer; no provider HTML is injected.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from lxml import etree

from proof_index.hashing import sha256_hex

ATOM = "http://www.w3.org/2005/Atom"
MAX_ENTRIES = 100


def _el(parent: etree._Element, tag: str, text: str | None = None, **attrs: str) -> etree._Element:
    node = etree.SubElement(parent, f"{{{ATOM}}}{tag}", attrs)
    if text is not None:
        node.text = text
    return node


def tag_uri(site_url: str, kind: str, key: str) -> str:
    host = urlsplit(site_url).hostname or "localhost"
    return f"tag:{host},2026:{kind}:{key}"


def build_feed(
    *,
    kind: str,
    title: str,
    site_url: str,
    base_path: str,
    feed_path: str,
    updated: str,
    entries: list[dict[str, Any]],
) -> bytes:
    """``entries``: dicts with id_key, title, path, date (YYYY-MM-DD), summary, categories."""
    root = etree.Element(f"{{{ATOM}}}feed", nsmap={None: ATOM})
    _el(root, "id", tag_uri(site_url, f"feed-{kind}", base_path))
    _el(root, "title", title)
    _el(root, "updated", updated)
    _el(root, "link", rel="self", href=f"{site_url}{base_path}{feed_path}")
    _el(root, "link", rel="alternate", href=f"{site_url}{base_path}")
    for entry in entries[:MAX_ENTRIES]:
        node = _el(root, "entry")
        _el(node, "id", tag_uri(site_url, kind, entry["id_key"]))
        _el(node, "title", entry["title"])
        _el(node, "link", rel="alternate", href=f"{site_url}{entry['path']}")
        _el(node, "updated", f"{entry['date']}T00:00:00Z")
        _el(node, "summary", entry["summary"])
        for category in entry.get("categories", []):
            _el(node, "category", term=category)
    data: bytes = etree.tostring(root, xml_declaration=True, encoding="utf-8", pretty_print=True)
    return data


def update_entry_key(event_key: str) -> str:
    return sha256_hex(event_key)[:24]
