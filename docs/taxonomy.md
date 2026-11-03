# Taxonomy and relevance rules

Source of truth: `config/taxonomy.yaml` (vocabulary, `taxonomy-v1`) and
`config/relevance-rules.yaml` (classification policy, `rules-v1`). IDs are stable;
display names may change. The taxonomy validator rejects unknown area, topic, or
contribution IDs, and the rule compiler rejects rules whose topic is unknown or
not under the rule's area.

## Vocabulary

- **Areas:** `proof-systems`, `security-proofs`, `formal-verification`.
- **Topics:** the 20 IDs of spec section 17. `quantum-security` sits under
  security-proofs and proof-systems; `computational-soundness` under
  formal-verification and security-proofs; `privacy-applications` under all three
  (evidence-dependent).
- **Contributions:** `construction-theory`, `analysis-attack`,
  `implementation-optimization`, `application`, `survey-tutorial`. Empty when
  evidence is insufficient; the UI then shows "Research paper".

## rules-v1 decision policy (as implemented in `classify/engine.py`)

Text is NFKC-normalized with unified dashes and collapsed whitespace; title and
abstract are matched separately. Reported offsets refer to this normalized text.
Two cues are *independent* only if they come from different evidence groups and
do not overlap in the same field.

1. A curation override, when present, decides (T09).
2. **proof-systems**: a `ps_direct` cue in the title; or a `ps_direct` abstract cue
   with an independent `crypto_context`/`ps_component` cue; or a `ps_component` cue
   with an independent `crypto_context` cue, one of them in the title.
3. **security-proofs**: a `security_method` cue with an independent
   `protocol_object`/`crypto_context` cue, one of them in the title; or a title
   "cryptanalysis of <name>" where the name is on the versioned allowlist.
4. **formal-verification**: a `formal_method`/`formal_tool` cue with an independent
   `crypto_context`/`protocol_object` cue, and either a formal cue in the title or
   two distinct formal cues in the abstract.
5. Supported area(s) and no contradictory context → `include` with every supported
   area. Supported area(s) plus a contradictory context → `review`.
6. Otherwise `review` when any weak cue exists:
   - a proof-system or security-method cue that lacked the required context;
   - a formal cue *with* cryptographic text context but insufficient formal evidence;
   - an ambiguous title token (`proof`, `argument`, `security`, `verification`,
     `theorem`, bare acronyms such as PCP/IOP/MPC/UC, `Nova`, `AIR`, `Halo`,
     `Spartan`, `folding`) together with cryptographic text evidence, or with
     venue evidence when the abstract is missing;
   - abstract missing while the source/venue indicates cryptography (ePrint, or a
     venue marked `cryptographic` in `config/venues.yaml`).
7. Otherwise `exclude`. Ambiguous tokens alone never force review. Formal-tool or
   formal-method cues with no cryptographic evidence are excluded (F07, F15).

Contradictory contexts (astronomy, aerodynamics, protein chemistry) suppress only
the matching ambiguous names (`Nova`, `AIR`, `folding`). They never exclude a paper
whose other evidence supports an area; they turn `include` into `review`.

### Deliberate scope choices to evaluate in T18

- Cryptographic context alone (e.g. "a new lattice-based signature scheme" with no
  security-method phrase) is **excluded**, not reviewed. Otherwise almost every
  ePrint paper would enter review. Recall on such papers must be measured in T18.
- `interactive proof(s)` is a `ps_direct` cue (an addition to the starter list in
  spec 10.2) so foundational interactive-proof titles are not missed.
- `STARK`, `GKR`, `FRI`, `KEM`, `IVC`, tool names, and protocol names are
  case-sensitive to avoid ordinary words ("stark contrast", "Friday").

## Work-level aggregation (T08)

A work is `included` if any current source record is included, else `review` if any
is review, else `excluded`, subject to the latest explicit work override. Area and
topic labels are the union over current valid source records.
