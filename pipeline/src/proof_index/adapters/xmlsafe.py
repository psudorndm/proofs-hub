"""Secure lxml parsing for provider responses (spec section 2: XML).

No DTD loading, no entity resolution, no network access. Any DOCTYPE in a
provider page is rejected; DBLP's dump uses its own constrained parser (T10).
"""

from __future__ import annotations

from lxml import etree

from proof_index.adapters.base import PageStructureError

_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
    remove_blank_text=False,
)


def parse_xml(body: bytes, *, expect_root: tuple[str, str] | None = None) -> etree._Element:
    """Parse ``body``; optionally require root ``(namespace_uri, local_name)``."""
    if not body or not body.strip():
        raise PageStructureError("empty response body")
    head = body.lstrip()[:512].lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        raise PageStructureError("received an HTML page instead of XML")
    try:
        root = etree.fromstring(body, parser=_PARSER)
    except etree.XMLSyntaxError as exc:
        raise PageStructureError(f"malformed or truncated XML ({exc.msg[:120]})") from None
    tree = root.getroottree()
    if tree.docinfo.doctype:
        raise PageStructureError("XML DOCTYPE declarations are not accepted in provider pages")
    if expect_root is not None:
        qname = etree.QName(root)
        if (qname.namespace, qname.localname) != expect_root:
            raise PageStructureError(
                f"unexpected root element {{{qname.namespace}}}{qname.localname}"
            )
    return root


def text_of(element: etree._Element | None) -> str | None:
    """All descendant text (inline markup flattened), or ``None``."""
    if element is None:
        return None
    joined = "".join(element.itertext())
    return joined if joined.strip() else None


def local_path(element: etree._Element) -> str:
    """Namespace-free path for evidence strings (e.g. ``record/metadata/dc/title``)."""
    parts: list[str] = []
    node: etree._Element | None = element
    while node is not None:
        parts.append(etree.QName(node).localname)
        node = node.getparent()
    return "/".join(reversed(parts))
