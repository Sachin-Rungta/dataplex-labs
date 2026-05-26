# Evaluation Assets

This directory holds the frozen evaluation inputs, expected outputs, and
scoring rubrics for the Business Glossary Agent. See `../DESIGN.md` §6
for the full eval framework.

## Layout

```
eval/
├── README.md                 (this file)
├── golden/
│   ├── gs_ontology_v0/       Term/category gold for ~3 domains
│   ├── gs_links_v0/          (term, entry) -> relationship labels
│   ├── gs_ingestion_v0/      Expected concepts per source doc
│   └── gs_nl_questions_v0/   NL questions + expected SQL/answer
├── rubrics/
│   ├── category_coherence.md (1-5 scoring guide)
│   ├── definition_quality.md (1-5 scoring guide)
│   └── answer_correctness.md (binary judging guide)
├── harness/
│   ├── run_ontology_eval.py  Compares agent output to gs_ontology_v0
│   ├── run_link_eval.py      Computes P/R/F1 + relationship accuracy
│   └── run_uplift_ab.py      A/B harness for §6.4
└── reports/                  Generated per-run reports (gitignored)
```

## Authoring rules for golden sets

1. **Two-rater minimum.** Every label needs sign-off from at least two
   reviewers (one engineer, one domain SME). Disagreements resolved by
   a third reviewer.
2. **Source-anchored.** Every gold term must cite the catalog entry or
   GCS doc URI that justifies it. No labels-from-thin-air.
3. **Frozen, then versioned.** Once a golden set is published (`v0`,
   `v1`, ...) it does not change. New labels go into the next version.
   Scores across versions are not directly comparable.
4. **Stratification baked in.** For `gs_links_v0`, every (term, entry)
   pair carries a `difficulty` tag (`obvious | semantic | distant`) so
   we can report stratified metrics, not just aggregate.

## CI integration

Every PR that touches `knowledge_catalog_business_glossary_agent/` runs:
- `pytest tests/`  (unit + mocked-integration)
- `python harness/run_ontology_eval.py --golden gs_ontology_v0 --fail-below 0.65`
- `python harness/run_link_eval.py --golden gs_links_v0 --fail-below 0.60`

The uplift A/B (`run_uplift_ab.py`) runs nightly, not per-PR — it's
slow and expensive. Its dashboard is the source of truth for §6.4.
