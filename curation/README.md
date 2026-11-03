# Curation

- `foundations.jsonl` — the curated foundational collection, one JSON object per
  line in the format of spec section 9. Every entry needs verified bibliographic
  fields and real evidence URLs; `proof-index import-curated` rejects placeholder
  `example.*` URLs and the spec's schema example. **The file is empty until entries
  are verified** (task T17); no entry has been added from unverified memory.
- `overrides/` — curator command YAML files (`proof-index curate validate|apply`).
- `evaluation/` — labeling samples and held-out splits (task T18).

`review_kind` is `agent-provisional` for anything an agent prepared and
`human-reviewed` only when a person actually reviewed it.
