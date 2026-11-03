"""BibTeX citations (spec sections 13–14). Author order is preserved; special
characters are escaped; citation keys are derived from the stable work ID."""

from __future__ import annotations

from typing import Any

from proof_index.normalize.identifiers import eprint_display_id

_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape(text: str) -> str:
    return "".join(_ESCAPES.get(ch, ch) for ch in text)


def citation_key(work_id: str) -> str:
    return f"cpri-{work_id.split('-')[0]}"


def bibtex_entry(paper: dict[str, Any]) -> str:
    pubs = [p for p in paper["publications"] if p.get("venue_name")]
    venue = pubs[0] if pubs else None
    venue_name = venue["venue_name"] if venue else None
    venue_key = (venue or {}).get("venue_key") or ""
    if venue_name and venue_key.startswith("journals/"):
        kind, venue_field = "article", "journal"
    elif venue_name:
        kind, venue_field = "inproceedings", "booktitle"
    else:
        kind, venue_field = "misc", None
    fields: list[tuple[str, str]] = [("title", "{" + escape(paper["title"]) + "}")]
    if paper["authors"]:
        fields.append(("author", " and ".join(escape(a["display_name"]) for a in paper["authors"])))
    published = paper["published"]
    if published["year"] is not None:
        fields.append(("year", str(published["year"])))
        if published["month"] is not None:
            fields.append(("month", str(published["month"])))
    if venue_field and venue_name:
        fields.append((venue_field, escape(venue_name)))
    doi = next((p["doi"] for p in paper["publications"] if p.get("doi")), None)
    if doi:
        fields.append(("doi", escape(doi)))
    notes = []
    for source in paper["sources"]:
        if source["provider"] == "eprint":
            notes.append(
                f"Cryptology ePrint Archive, Paper {eprint_display_id(source['source_id'])}"
            )
        elif source["provider"] == "arxiv":
            fields.append(("eprint", escape(source["source_id"])))
            fields.append(("archiveprefix", "arXiv"))
    if notes:
        fields.append(("note", escape("; ".join(notes))))
    landing = next((s["landing_url"] for s in paper["sources"] if s.get("landing_url")), None)
    if landing:
        fields.append(("url", landing.replace("%", r"\%").replace("#", r"\#")))
    body = ",\n".join(
        f"  {name} = {{{value}}}" if not value.startswith("{") else f"  {name} = {value}"
        for name, value in fields
    )
    return f"@{kind}{{{citation_key(paper['id'])},\n{body}\n}}\n"
