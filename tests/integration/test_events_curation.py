"""Events (spec 11.3) and curator commands (11.4) on PostgreSQL."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from proof_index.db import tables as t
from proof_index.models.classification import load_taxonomy
from proof_index.resolve.candidates import resolve_live
from proof_index.resolve.curation import (
    CurationConflict,
    CurationService,
    StaleRevisionError,
    parse_command,
)

from .harness import CASES, ROOT, Harness, mutate

pytestmark = pytest.mark.db


def events(harness: Harness, work: Any = None) -> list[tuple[str, str | None, str, bool]]:
    with harness.engine.connect() as conn:
        q = select(
            t.work_events.c.kind,
            t.work_events.c.event_date,
            t.work_events.c.date_basis,
            t.work_events.c.feed_eligible,
        ).order_by(t.work_events.c.kind, t.work_events.c.event_date)
        if work is not None:
            q = q.where(t.work_events.c.work_id == work)
        return [
            (
                r.kind,
                r.event_date.isoformat() if r.event_date else None,
                r.date_basis,
                r.feed_eligible,
            )
            for r in conn.execute(q)
        ]


# --- events ---------------------------------------------------------------------------------


def test_f03_revision_is_one_supported_event_and_keeps_publication(h: Harness) -> None:
    h.ingest(CASES["F01"], CASES["F02"])
    h.ingest(CASES["F03"])
    work = h.work_of("eprint", "2020/7")
    assert work == h.work_of("arxiv", "2001.00007")
    assert events(h, work) == [
        ("publication", "2020-01-03", "source", True),
        ("source_added", "2026-11-03", "observed", False),
        ("source_revision", "2020-03-01", "source", True),
    ]
    # Re-running the same responses does not create further events.
    h.ingest(CASES["F03"])
    assert len(events(h, work)) == 3


def test_source_added_does_not_reset_original_publication(h: Harness) -> None:
    h.ingest(CASES["F02"])  # arXiv first: publication 2020-01-05
    work = h.work_of("arxiv", "2001.00007")
    h.ingest(CASES["F01"])  # ePrint attaches later with an earlier date
    kinds = {k: d for k, d, _, _ in events(h, work)}
    assert kinds["publication"] == "2020-01-03"  # corrected to the earliest supported date
    assert "source_added" in kinds
    with h.engine.connect() as conn:
        pubs = conn.execute(
            select(t.work_events).where(t.work_events.c.kind == "publication")
        ).all()
    assert len(pubs) == 1


def test_bootstrap_first_observation_creates_no_update_events(h: Harness) -> None:
    h.ingest(CASES["F14"], bootstrap=True)
    work = h.work_of("eprint", "1999/1")
    assert events(h, work) == [("publication", "1999-01-05", "source", True)]


def test_metadata_update_without_version_after_baseline(h: Harness) -> None:
    h.ingest(CASES["F04"])
    work = h.work_of("eprint", "2021/42")
    h.ingest(mutate(CASES["F04"], abstract=CASES["F04"]["abstract"] + " Corrected."))
    assert ("metadata_updated", "2026-11-03", "observed", True) in events(h, work)


def test_metadata_change_during_bootstrap_has_no_observed_event(h: Harness) -> None:
    h.ingest(CASES["F04"], bootstrap=True)
    work = h.work_of("eprint", "2021/42")
    h.ingest(mutate(CASES["F04"], abstract="Changed during bootstrap."), bootstrap=True)
    assert [e[0] for e in events(h, work)] == ["publication"]


def test_whitespace_only_change_creates_no_event(h: Harness) -> None:
    h.ingest(CASES["F04"])
    work = h.work_of("eprint", "2021/42")
    h.ingest(mutate(CASES["F04"], title="  Security   proofs for key encapsulation  mechanisms "))
    assert [e[0] for e in events(h, work)] == ["publication"]


def test_withdrawal_and_restoration_events(h: Harness) -> None:
    h.ingest(CASES["F13"])
    work = h.work_of("eprint", "2023/6")
    h.ingest(mutate(CASES["F13"], status="withdrawn", source_modified_at="2026-11-02T00:00:00Z"))
    h.ingest(mutate(CASES["F13"], status="active", source_modified_at="2026-11-03T00:00:00Z"))
    kinds = [(k, d) for k, d, _, _ in events(h, work) if k in ("withdrawn", "restored")]
    assert kinds == [("restored", "2026-11-03"), ("withdrawn", "2026-11-02")]
    assert h.canonical(work)["status"] == "active"


def test_event_keys_are_stable_and_unique(h: Harness) -> None:
    h.ingest(CASES["F01"], CASES["F02"])
    h.ingest(CASES["F03"])
    with h.engine.connect() as conn:
        keys = conn.execute(select(t.work_events.c.event_key)).scalars().all()
    assert len(keys) == len(set(keys))
    work = h.work_of("eprint", "2020/7")
    assert f"publication:{work}" in keys


# --- curation ---------------------------------------------------------------------------------


def service(h: Harness) -> CurationService:
    return CurationService(h.clock, h.builder, load_taxonomy(ROOT / "config/taxonomy.yaml"))


def cmd(**data: Any) -> Any:
    base = {"reason": "test reason", "reviewer_label": "tester", "review_kind": "agent-provisional"}
    base.update({k: str(v) if hasattr(v, "hex") else v for k, v in data.items()})
    return parse_command(base)


def apply(h: Harness, command: Any) -> dict[str, Any]:
    with h.engine.begin() as conn:
        return service(h).apply(conn, command)


def rev(h: Harness, work: Any) -> int:
    return int(h.canonical(work)["revision"])


def test_include_command_idempotent_and_id_reuse_conflict(h: Harness) -> None:
    h.ingest(CASES["F08"])
    work = h.work_of("dblp", "conf/synthcrypto/Generic20")
    c = cmd(
        command_id="inc-1", action="include", work_id=work, expected_canonical_revision=rev(h, work)
    )
    assert apply(h, c)["status"] == "applied"
    canon = h.canonical(work)
    assert canon["visibility"] == "included"
    assert canon["inclusion_provenance"] == "agent-provisional"
    assert apply(h, c)["status"] == "already_applied"
    different = cmd(
        command_id="inc-1", action="exclude", work_id=work, expected_canonical_revision=rev(h, work)
    )
    with pytest.raises(CurationConflict):
        apply(h, different)


def test_stale_curation_revision_rejects_write(h: Harness) -> None:
    h.ingest(CASES["F04"])
    work = h.work_of("eprint", "2021/42")
    stale = rev(h, work)
    h.ingest(mutate(CASES["F04"], abstract="An intervening source update."))
    with pytest.raises(StaleRevisionError, match="expected"):
        apply(
            h,
            cmd(
                command_id="ex-1", action="exclude", work_id=work, expected_canonical_revision=stale
            ),
        )
    assert h.canonical(work)["visibility"] == "included"


def test_set_fields_override_wins_with_curation_provenance(h: Harness) -> None:
    h.ingest(CASES["F04"])
    work = h.work_of("eprint", "2021/42")
    apply(
        h,
        cmd(
            command_id="sf-1",
            action="set_fields",
            work_id=work,
            expected_canonical_revision=rev(h, work),
            fields={"title": "Corrected title"},
        ),
    )
    canon = h.canonical(work)
    assert canon["title"] == "Corrected title"
    assert canon["title_provenance"] == {
        "origin": "curation",
        "field": "title",
        "command_id": "sf-1",
        "source_record_id": None,
        "observation_id": None,
    }


def test_set_labels_rejects_unknown_ids() -> None:
    from proof_index.errors import InputError

    with pytest.raises(InputError):
        parse_command(
            {
                "command_id": "x-1",
                "action": "set_labels",
                "reason": "r r r",
                "reviewer_label": "t",
                "review_kind": "human-reviewed",
                "work_id": "00000000-0000-0000-0000-000000000001",
                "expected_canonical_revision": 1,
                "areas": ["cryptography"],
            }
        )


def _two_works(h: Harness) -> tuple[Any, Any]:
    h.ingest(CASES["F01"])
    h.ingest(CASES["F10"])
    return h.work_of("eprint", "2020/7"), h.work_of("eprint", "2020/9")


def test_merge_moves_sources_and_alias_resolves(h: Harness) -> None:
    a, b = _two_works(h)
    apply(
        h,
        cmd(
            command_id="m-1",
            action="merge",
            survivor=a,
            absorbed=[str(b)],
            expected_canonical_revision={str(a): rev(h, a), str(b): rev(h, b)},
        ),
    )
    assert h.work_of("eprint", "2020/9") == a
    with h.engine.connect() as conn:
        assert resolve_live(conn, b) == a
        override = conn.execute(select(t.source_assignment_overrides)).one()
    assert override.target_work_id == a
    assert len(h.canonical(a)["sources"]) == 2


def test_merge_aliases_are_acyclic_and_resolve_to_live_work(h: Harness) -> None:
    a, b = _two_works(h)
    h.ingest(CASES["F04"])
    c_work = h.work_of("eprint", "2021/42")
    apply(
        h,
        cmd(
            command_id="m-1",
            action="merge",
            survivor=b,
            absorbed=[str(a)],
            expected_canonical_revision={str(a): rev(h, a), str(b): rev(h, b)},
        ),
    )
    apply(
        h,
        cmd(
            command_id="m-2",
            action="merge",
            survivor=c_work,
            absorbed=[str(b)],
            expected_canonical_revision={str(b): rev(h, b), str(c_work): rev(h, c_work)},
        ),
    )
    with h.engine.connect() as conn:
        assert resolve_live(conn, a) == c_work
    # Merging the live work into a dead alias is refused (no cycle can form).
    with pytest.raises(StaleRevisionError, match="merged into"):
        apply(
            h,
            cmd(
                command_id="m-3",
                action="merge",
                survivor=a,
                absorbed=[str(c_work)],
                expected_canonical_revision={str(a): 0, str(c_work): rev(h, c_work)},
            ),
        )


def test_manual_split_survives_reclassification_and_resolution(h: Harness) -> None:
    h.ingest(CASES["F01"], CASES["F02"])
    work = h.work_of("eprint", "2020/7")
    with h.engine.connect() as conn:
        ids = {r.source: r.id for r in conn.execute(select(t.source_records))}
    apply(
        h,
        cmd(
            command_id="sp-1",
            action="split",
            work_id=work,
            expected_canonical_revision=rev(h, work),
            assignments={str(ids["eprint"]): "keep", str(ids["arxiv"]): "new:arxiv-version"},
        ),
    )
    new = h.work_of("arxiv", "2001.00007")
    assert new != work and h.work_of("eprint", "2020/7") == work
    # Later source updates and reclassification do not undo the split.
    h.ingest(CASES["F03"])
    h.ingest(mutate(CASES["F01"], abstract=CASES["F01"]["abstract"] + " v2"))
    assert h.work_of("arxiv", "2001.00007") == new
    assert h.work_of("eprint", "2020/7") == work
    with pytest.raises(CurationConflict, match="separation"):
        apply(
            h,
            cmd(
                command_id="m-9",
                action="merge",
                survivor=work,
                absorbed=[str(new)],
                expected_canonical_revision={str(work): rev(h, work), str(new): rev(h, new)},
            ),
        )


def test_split_requires_complete_assignments(h: Harness) -> None:
    from proof_index.errors import InputError

    h.ingest(CASES["F01"], CASES["F02"])
    work = h.work_of("eprint", "2020/7")
    with h.engine.connect() as conn:
        eprint_id = conn.execute(
            select(t.source_records.c.id).where(t.source_records.c.source == "eprint")
        ).scalar_one()
    with pytest.raises(InputError, match="every source record"):
        apply(
            h,
            cmd(
                command_id="sp-2",
                action="split",
                work_id=work,
                expected_canonical_revision=rev(h, work),
                assignments={str(eprint_id): "keep"},
            ),
        )


def test_revert_include_restores_automatic_decision(h: Harness) -> None:
    h.ingest(CASES["F08"])
    work = h.work_of("dblp", "conf/synthcrypto/Generic20")
    apply(
        h,
        cmd(
            command_id="inc-1",
            action="include",
            work_id=work,
            expected_canonical_revision=rev(h, work),
        ),
    )
    apply(h, cmd(command_id="rv-1", action="revert", reverts="inc-1"))
    assert h.canonical(work)["visibility"] == "review"
    with pytest.raises(CurationConflict, match="already reverted"):
        apply(h, cmd(command_id="rv-2", action="revert", reverts="inc-1"))


def test_revert_preserves_later_independent_source_observations(h: Harness) -> None:
    a, b = _two_works(h)
    apply(
        h,
        cmd(
            command_id="m-1",
            action="merge",
            survivor=a,
            absorbed=[str(b)],
            expected_canonical_revision={str(a): rev(h, a), str(b): rev(h, b)},
        ),
    )
    # An independent source update arrives for a moved record after the merge.
    h.ingest(mutate(CASES["F10"], abstract="Newer abstract observed after the merge."))
    apply(h, cmd(command_id="rv-1", action="revert", reverts="m-1"))
    assert h.work_of("eprint", "2020/9") == b
    with h.engine.connect() as conn:
        assert resolve_live(conn, b) == b
        assert conn.execute(select(t.source_assignment_overrides)).first() is None
    assert h.canonical(b)["abstract"] == "Newer abstract observed after the merge."


def test_relate_and_curation_events(h: Harness) -> None:
    a, b = _two_works(h)
    apply(
        h, cmd(command_id="rel-1", action="relate", from_work=a, to_work=b, relation="extended_by")
    )
    with h.engine.connect() as conn:
        assert conn.execute(select(t.work_relations.c.relation)).scalar_one() == "extended_by"
        kinds = conn.execute(
            select(t.work_events.c.kind).where(t.work_events.c.kind == "curation")
        ).all()
    assert len(kinds) == 2
    assert all(e[3] is False for e in events(h) if e[0] == "curation")
