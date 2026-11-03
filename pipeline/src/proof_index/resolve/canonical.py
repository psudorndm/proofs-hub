"""Rebuild a work's canonical projection (spec section 11.2).

Precedence:
* Explicit curated field override wins (T09 supplies ``CurationState``).
* Title/abstract: usable current preprint from ePrint, then arXiv; otherwise curated
  verified bibliography, then Crossref, then DBLP. Active sources before
  withdrawn/deleted ones.
* Authors come from the title's source when it has authors; lists are never stitched.
* DOI: Crossref, publication rows, curated, DBLP, arXiv, ePrint; disagreement is an issue.
* Original publication: earliest interval lower bound; ties prefer the more precise
  date (it cannot contradict an earlier manifestation); never sharpened otherwise.
* Links: deduplicated union with provenance.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import Connection, select, update

from proof_index.clock import Clock
from proof_index.db import tables as t
from proof_index.models.canonical import (
    CanonicalPublication,
    CanonicalSource,
    CanonicalWork,
    InclusionEvidence,
    Provenance,
    ProvenancedLink,
)
from proof_index.models.classification import AREAS, CONTRIBUTIONS
from proof_index.models.dates import PRECISION_RANK, PartialDate, is_future
from proof_index.models.source import Author, NormalizedRecord
from proof_index.normalize.identifiers import qualified_identifier

TEXT_RANK = {"eprint": 0, "arxiv": 1, "curated": 2, "crossref": 3, "dblp": 4}
DOI_RANK = {"crossref": 0, "curated": 1, "dblp": 2, "arxiv": 3, "eprint": 4}
AREA_GROUPS = {"ps_direct", "ps_component", "security_method", "formal_method", "formal_tool"}
DATE_CONFLICT_DAYS = 30


@dataclass
class CurationState:
    """Active curation affecting a work (filled by T09's curation module)."""

    visibility: str | None = None
    areas: list[str] | None = None
    topics: list[str] | None = None
    contributions: list[str] | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    command_ids: dict[str, str] = field(default_factory=dict)
    review_kind: str | None = None
    reason: str | None = None


@dataclass
class Member:
    record_id: uuid.UUID
    observation_id: uuid.UUID | None
    record: NormalizedRecord
    status: str  # source status, "deleted" for a tombstoned record
    first_seen: Any
    decision: str | None
    classification: Any  # classifications row or None


@dataclass
class RebuildResult:
    work_id: uuid.UUID
    changed: bool
    revision: int
    canonical: CanonicalWork | None
    previous: dict[str, Any]


def _members(conn: Connection, work_id: uuid.UUID, rules_version: str) -> list[Member]:
    rows = conn.execute(
        select(
            t.source_records.c.id,
            t.source_records.c.first_seen_at,
            t.source_records.c.current_observation_id,
            t.source_observations.c.normalized,
            t.source_observations.c.item_kind,
            t.source_observations.c.content_sha256,
        )
        .select_from(
            t.source_records.join(
                t.source_observations,
                t.source_observations.c.id == t.source_records.c.current_observation_id,
            )
        )
        .where(t.source_records.c.work_id == work_id)
        .order_by(
            t.source_records.c.first_seen_at,
            t.source_records.c.source,
            t.source_records.c.source_id,
        )
    ).all()
    members: list[Member] = []
    for row in rows:
        observation_id = row.current_observation_id
        if row.item_kind == "record":
            record = NormalizedRecord.model_validate(row.normalized)
            status = record.status
            content = row.content_sha256
        else:
            # Deleted at the provider: show the last known record content as deleted.
            prior = conn.execute(
                select(
                    t.source_observations.c.id,
                    t.source_observations.c.normalized,
                    t.source_observations.c.content_sha256,
                )
                .where(t.source_observations.c.source_record_id == row.id)
                .where(t.source_observations.c.item_kind == "record")
                .order_by(t.source_observations.c.observed_at.desc())
                .limit(1)
            ).first()
            if prior is None:
                continue
            record = NormalizedRecord.model_validate(prior.normalized)
            status, content, observation_id = "deleted", prior.content_sha256, prior.id
        cls = conn.execute(
            select(t.classifications)
            .where(t.classifications.c.source_record_id == row.id)
            .where(t.classifications.c.content_sha256 == content)
            .where(t.classifications.c.rules_version == rules_version)
        ).first()
        members.append(
            Member(
                row.id,
                observation_id,
                record,
                status,
                row.first_seen_at,
                cls.decision if cls is not None else None,
                cls,
            )
        )
    return members


def _prov(member: Member, field_name: str) -> Provenance:
    return Provenance(
        origin="source",
        source_record_id=str(member.record_id),
        observation_id=str(member.observation_id) if member.observation_id else None,
        field=field_name,
    )


def _text_order(m: Member) -> tuple[int, int, Any, str]:
    return (
        0 if m.status == "active" else 1,
        TEXT_RANK[m.record.source],
        m.first_seen,
        m.record.source_id,
    )


def _earliest(members: list[Member]) -> tuple[PartialDate, Member | None, list[str]]:
    dated = [(m.record.published, m) for m in members if m.record.published.date_interval()]
    issues: list[str] = []
    if not dated:
        return PartialDate.unknown(), None, issues

    def key(item: tuple[PartialDate, Member]) -> tuple[date, int]:
        interval = item[0].date_interval()
        assert interval is not None
        return interval[0], PRECISION_RANK[item[0].precision]

    chosen, member = min(dated, key=key)
    exact = sorted({d.exact() for d, _ in dated if d.exact() is not None})  # type: ignore[type-var]
    if len(exact) >= 2 and (exact[-1] - exact[0]).days > DATE_CONFLICT_DAYS:  # type: ignore[operator]
        issues.append("publication_dates_differ_across_sources")
    return chosen, member, issues


def build_canonical(
    members: list[Member],
    publications: list[CanonicalPublication],
    *,
    foundational: bool,
    curation: CurationState,
    today: date,
    previous_title: str | None,
) -> CanonicalWork:
    issues: list[str] = []
    ordered = sorted(members, key=_text_order)
    fields = curation.fields

    # Title, authors.
    if "title" in fields:
        title = str(fields["title"])
        title_prov = Provenance(
            origin="curation", field="title", command_id=curation.command_ids.get("title")
        )
        title_member = None
    elif ordered:
        title_member = ordered[0]
        title, title_prov = title_member.record.title, _prov(title_member, "title")
    else:
        title_member = None
        title = previous_title or "Untitled record"
        title_prov = Provenance(origin="source", field="title")
        issues.append("no_current_source_title")

    if "authors" in fields:
        authors = [Author.model_validate(a) for a in fields["authors"]]
        authors_prov: Provenance | None = Provenance(
            origin="curation", field="authors", command_id=curation.command_ids.get("authors")
        )
    else:
        source = (
            title_member
            if title_member and title_member.record.authors
            else next((m for m in ordered if m.record.authors), None)
        )
        authors = list(source.record.authors) if source else []
        authors_prov = _prov(source, "authors") if source else None
        if not authors:
            issues.append("authors_unavailable")

    if "abstract" in fields:
        abstract = fields["abstract"]
        abstract_prov: Provenance | None = Provenance(
            origin="curation", field="abstract", command_id=curation.command_ids.get("abstract")
        )
    else:
        with_abstract = next((m for m in ordered if m.record.abstract), None)
        abstract = with_abstract.record.abstract if with_abstract else None
        abstract_prov = _prov(with_abstract, "abstract") if with_abstract else None

    if "published" in fields:
        published = PartialDate.model_validate(fields["published"])
        published_prov: Provenance | None = Provenance(
            origin="curation", field="published", command_id=curation.command_ids.get("published")
        )
    else:
        published, date_member, date_issues = _earliest(members)
        issues += date_issues
        published_prov = _prov(date_member, "published") if date_member else None
    if is_future(published, today):
        issues.append("future_publication_date")

    # DOI.
    doi_candidates = sorted(
        ((DOI_RANK[m.record.source], m.record.doi) for m in members if m.record.doi),
        key=lambda x: x[0],
    )
    doi_values = [d for _, d in doi_candidates] + [p.doi for p in publications if p.doi]
    if "doi" in fields:
        doi = fields["doi"]
    else:
        doi = next((p.doi for p in publications if p.doi), None) or (
            doi_candidates[0][1] if doi_candidates else None
        )
    if len(set(doi_values)) > 1:
        issues.append("conflicting_dois")

    # Sources, links, identifiers.
    sources = [
        CanonicalSource(
            provider=m.record.source,
            source_id=m.record.source_id,
            source_record_id=str(m.record_id),
            observation_id=str(m.observation_id) if m.observation_id else None,
            version=m.record.source_version,
            status="deleted" if m.status == "deleted" else m.record.status,
            links=list(m.record.links),
            categories=list(m.record.source_categories),
        )
        for m in sorted(members, key=lambda m: (m.record.source, m.record.source_id))
    ]
    seen_links: set[str] = set()
    links: list[ProvenancedLink] = []
    for m in ordered:
        for link in m.record.links:
            if link.url not in seen_links:
                seen_links.add(link.url)
                links.append(
                    ProvenancedLink(
                        link=link, source_record_id=str(m.record_id), provider=m.record.source
                    )
                )
    identifiers = sorted(
        {qualified_identifier(m.record.source, m.record.source_id) for m in members}
        | {f"doi:{d}" for d in doi_values if d}
    )
    if any(m.status != "active" for m in members) and any(m.status == "active" for m in members):
        issues.append("some_sources_withdrawn_or_deleted")

    # Status.
    statuses = {m.status for m in members}
    if "active" in statuses:
        record_status = "active"
    elif "withdrawn" in statuses:
        record_status = "withdrawn"
    else:
        record_status = "unavailable"

    # Classification aggregation.
    decisions = {m.decision for m in members}
    visibility = (
        "included" if "include" in decisions else "review" if "review" in decisions else "excluded"
    )
    labelled = [m for m in members if m.decision in ("include", "review") and m.classification]
    areas = sorted({a for m in labelled for a in m.classification.areas}, key=AREAS.index)
    topics = sorted({tp for m in labelled for tp in m.classification.topics})
    contributions = sorted(
        {x for m in labelled for x in m.classification.contributions}, key=CONTRIBUTIONS.index
    )
    provenance = "automatic"
    evidence: list[InclusionEvidence] = []
    seen_evidence: set[tuple[str, str, str]] = set()
    for m in labelled:
        for hit in m.classification.evidence.get("matched_rules", []):
            marker = (hit["field"], hit["snippet"], hit["rule_id"])
            if marker in seen_evidence:
                continue
            seen_evidence.add(marker)
            if hit["evidence_group"] in AREA_GROUPS and len(evidence) < 8:
                evidence.append(
                    InclusionEvidence(
                        source_record_id=str(m.record_id),
                        field=hit["field"],
                        snippet=hit["snippet"],
                        rule_id=hit["rule_id"],
                    )
                )
    rules = sorted({m.classification.rules_version for m in labelled if m.classification})
    if visibility == "included":
        explanation = (
            f"Included automatically by {', '.join(rules)}: "
            + ", ".join(areas or ["no area"])
            + " evidence in source metadata."
        )
    elif visibility == "review":
        explanation = "Awaiting review: relevance evidence is incomplete or conflicting."
    else:
        explanation = "Not included: no supported relevance evidence."

    if curation.visibility is not None:
        visibility = curation.visibility
        provenance = curation.review_kind or "agent-provisional"
        explanation = f"Curated decision ({provenance}): {curation.reason or 'no reason given'}"
    if curation.areas is not None:
        areas = list(curation.areas)
        provenance = curation.review_kind or "agent-provisional"
    if curation.topics is not None:
        topics = sorted(curation.topics)
    if curation.contributions is not None:
        contributions = list(curation.contributions)

    return CanonicalWork(
        title=title,
        title_provenance=title_prov,
        authors=authors,
        authors_provenance=authors_prov,
        abstract=abstract,
        abstract_provenance=abstract_prov,
        published=published,
        published_provenance=published_prov,
        doi=doi,
        identifiers=identifiers,
        sources=sources,
        publications=publications,
        links=links,
        visibility=visibility,
        areas=areas,
        topics=topics,
        contributions=contributions,
        inclusion_provenance=provenance,
        inclusion_explanation=explanation,
        inclusion_evidence=evidence,
        record_status=record_status,
        foundational=foundational,
        metadata_issues=sorted(set(issues)),
    )


def _publications(
    conn: Connection, work_id: uuid.UUID, members: list[Member]
) -> list[CanonicalPublication]:
    pubs = [
        CanonicalPublication(
            provider=row.provider,
            provider_key=row.provider_key,
            venue_key=row.metadata.get("venue_key"),
            venue_name=row.metadata.get("venue_name"),
            year=row.metadata.get("year"),
            doi=row.doi,
        )
        for row in conn.execute(
            select(t.publications)
            .where(t.publications.c.work_id == work_id)
            .order_by(t.publications.c.provider, t.publications.c.provider_key)
        ).all()
    ]
    known = {(p.provider, p.provider_key) for p in pubs}
    for m in members:
        if (
            m.record.source in ("dblp", "crossref")
            and m.record.venue_name
            and (m.record.source, m.record.source_id) not in known
        ):
            pubs.append(
                CanonicalPublication(
                    provider=m.record.source,
                    provider_key=m.record.source_id,
                    venue_key=m.record.venue_key,
                    venue_name=m.record.venue_name,
                    year=m.record.published.year,
                    doi=m.record.doi,
                )
            )
    return pubs


CurationLoader = Any  # Callable[[Connection, uuid.UUID], CurationState]


class CanonicalBuilder:
    def __init__(
        self, clock: Clock, rules_version: str, curation_loader: CurationLoader | None = None
    ) -> None:
        self.clock = clock
        self.rules_version = rules_version
        self.curation_loader = curation_loader

    def rebuild(
        self, conn: Connection, work_id: uuid.UUID, pending: list[dict[str, Any]] | None = None
    ) -> RebuildResult:
        work = conn.execute(select(t.works).where(t.works.c.id == work_id).with_for_update()).one()
        previous = dict(work.canonical or {})
        if work.merged_into is not None:
            return RebuildResult(work_id, False, work.canonical_revision, None, previous)
        members = _members(conn, work_id, self.rules_version)
        curation = (
            self.curation_loader(conn, work_id, pending)
            if self.curation_loader
            else CurationState()
        )
        canonical = build_canonical(
            members,
            _publications(conn, work_id, members),
            foundational=work.is_foundational,
            curation=curation,
            today=self.clock.today(),
            previous_title=previous.get("title"),
        )
        new_hash = canonical.public_hash()
        old_hash = previous.get("_public_hash")
        changed = new_hash != old_hash
        revision = work.canonical_revision + (1 if changed else 0)
        payload = canonical.model_dump(mode="json")
        payload["_public_hash"] = new_hash
        values: dict[str, Any] = {
            "canonical": payload,
            "visibility": canonical.visibility,
            "record_status": canonical.record_status,
        }
        if changed:
            values.update(canonical_revision=revision, updated_at=self.clock.now())
        conn.execute(update(t.works).where(t.works.c.id == work_id).values(**values))
        return RebuildResult(work_id, changed, revision, canonical, previous)
