from proof_index.publish.bibtex import bibtex_entry, escape


def paper(**changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "0302f092-1111-2222-3333-444455556666",
        "title": "Proofs & {arguments}: 100% of $x_1$ #1 ~ ^",
        "authors": [
            {"display_name": "Zoë Ångström"},
            {"display_name": "Ann_B"},
            {"display_name": "Al"},
        ],
        "published": {"year": 2021, "month": 3, "day": None, "precision": "month"},
        "publications": [
            {
                "venue_key": "journals/joc",
                "venue_name": "J. Cryptol.",
                "year": 2021,
                "doi": "10.9999/x_y",
            }
        ],
        "sources": [
            {
                "provider": "eprint",
                "source_id": "2021/42",
                "landing_url": "https://eprint.iacr.org/2021/042",
            }
        ],
    }
    data.update(changes)
    return data


def test_bibtex_escapes_special_characters_and_preserves_author_order() -> None:
    entry = bibtex_entry(paper())
    assert entry.startswith("@article{cpri-0302f092,")
    assert (
        r"title = {Proofs \& \{arguments\}: 100\% of \$x\_1\$ \#1 \textasciitilde{} \textasciicircum{}}"
        in entry
    )
    assert "author = {Zoë Ångström and Ann\\_B and Al}" in entry
    assert "journal = {J. Cryptol.}" in entry
    assert "doi = {10.9999/x\\_y}" in entry
    assert "note = {Cryptology ePrint Archive, Paper 2021/042}" in entry


def test_bibtex_misc_without_venue_and_no_invented_fields() -> None:
    entry = bibtex_entry(
        paper(
            publications=[],
            published={"year": None, "month": None, "day": None, "precision": "unknown"},
            authors=[],
        )
    )
    assert entry.startswith("@misc{")
    assert "year" not in entry and "author" not in entry


def test_escape_backslash() -> None:
    assert escape("a\\b") == r"a\textbackslash{}b"
