# Evaluation Harness

One Python script. Four scenarios per domain. Every golden set under
`eval/golden/<domain>/expected.yaml` runs automatically. One combined
report covering all domains.

```
eval/
├── run_eval.py                          # the script you run
├── golden/
│   ├── customer_360/expected.yaml       # 18 terms, 5 categories — the ground truth
│   └── supply_chain/expected.yaml       # second domain (used by --query freeform)
├── golden_set.py                        # YAML schema + loader
├── headless_recommender.py              # invokes the agent pipeline without ADK
├── judges.py                            # LLM-as-Judge functions
├── metrics.py                           # P / R / F1 / link recall
├── report.py                            # markdown + JSON renderers
└── results/                             # generated per-run reports (gitignored)
```

## The four scenarios

| # | Scenario | Mode | GCS context | Existing glossary |
| -: | --- | --- | --- | --- |
| 1 | NEW glossary, NL only | `new` | none | n/a |
| 2 | NEW glossary, NL + GCS docs | `new` | yes | n/a |
| 3 | EXTEND existing glossary, catalog only | `extend` | none | auto-seeded |
| 4 | EXTEND existing glossary, NL + GCS | `extend` | yes | auto-seeded |

For scenarios 3 + 4 the script auto-creates a partial-coverage
"starter glossary" per domain. The customer-360 starter has two
categories (*Customer Profile*, *Subscription*) and six seed terms
(*Customer*, *Customer Segment*, *Customer Lifetime Value*,
*Subscription Account*, *Plan Tier*, *Monthly Recurring Revenue*).
The supply-chain starter has two categories (*Suppliers*,
*Procurement*) and four seed terms (*Supplier*, *Strategic
Supplier*, *Purchase Order*, *Committed Spend*). Both are
intentionally partial coverage of their golden set so the agent has
both real duplicates to skip and obvious gaps to fill. Pass
`--no-seed-glossary` if you'd rather point at a glossary you've
built yourself.

## Running

```bash
cd <agent-repo-root>
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)

# One-time: seed the synthetic data
./test_fixtures/seed_synthetic_data.sh --project=$GOOGLE_CLOUD_PROJECT
# wait 5-15 min for Knowledge Catalog to index

# Every golden set, all four scenarios each, with LLM-as-Judge:
python -m eval.run_eval

# Limit to one domain (golden-set key):
python -m eval.run_eval --domain customer_360
python -m eval.run_eval --domain customer_360 --domain supply_chain   # multi

# Limit to one scenario (works across domains):
python -m eval.run_eval --scenario 2
python -m eval.run_eval --scenario customer_360/2-new-with-gcs

# Fast / free run — structural metrics only, no LLM judging:
python -m eval.run_eval --skip-judges

# Freeform: bring your own query
python -m eval.run_eval \
    --query "Recommend a glossary for our marketing domain" \
    --mode new --scope-hint "marketing campaign lead funnel" \
    --golden-domain customer_360
```

## What lands in `eval/results/`

- `eval-<timestamp>.md` — human-readable report with summary table + per-scenario detail.
- `eval-<timestamp>.json` — same data, machine-shaped, including every judge verdict and false-positive / false-negative breakdown.
- `latest.md` / `latest.json` — overwritten with the most recent run.

The summary table also prints to stdout so you can eyeball it without
opening the file.

## Metric guide

| Metric | What it answers |
| --- | --- |
| Term precision | Of the agent's terms, how many does the LLM judge accept as matching a golden term? |
| Term recall | Of the golden terms, how many did the agent surface (possibly under an alias)? |
| Term F1 | Harmonic mean of P and R. |
| P@10 | Precision restricted to the agent's top-10 terms. Use this to check whether the *most confident* terms are good. |
| Cats matched | How many golden categories were surfaced by name or alias / cosine. |
| Coherence (mean) | 1–5 LLM rubric: are the terms within each category coherent? |
| Definitions (mean) | 1–5 LLM rubric: is each term's definition accurate and non-tautological? |
| Link recall | Of the golden `must_link` tuples, how many did the agent propose? |
| Rel acc | Of the correctly-recalled links, what fraction had the right relationship (`definition` / `synonym` / `related` / `schema-join`)? |

## Editing the golden sets

`eval/golden/customer_360/expected.yaml` is where the customer-360
ground truth lives. To grade against your own expectations, edit:

- `expected_categories[]` — every category needs `display_name`,
  `description`, and an `aliases[]` (alternative names that should
  also count as a match).
- `expected_terms[]` — same idea, plus `expected_category`,
  `description`, and an optional `must_link[]` array of
  `{entry_suffix, relationship}` tuples. The harness matches link
  targets by *suffix* so `customer_360.customers` matches the full
  `projects/.../entries/.../customer_360.customers` resource name.

## Caveats

- The harness bypasses ADK's runner and calls Gemini directly with the
  same prompts the live agent uses. That means it tests *recommendation
  quality* deterministically — but does not exercise the root-agent
  orchestration or the steward approval gates. Run `adk run .` for
  those.
- Judge calls cost money. A full four-scenario run with judging on is
  ~12 LLM calls (3 rubrics × 4 scenarios) on Gemini Flash, each with
  a short prompt. Cheap, but not free. Use `--skip-judges` when
  iterating on prompts.
- For extend scenarios, the starter glossary is created in the
  `DATAPLEX_GLOSSARY_LOCATION` (default `global`). If your scenario
  needs a different location, set the env var before running.

## Adding a new domain

1. Drop an `expected.yaml` under `eval/golden/<your-domain>/` modelled
   on `customer_360/expected.yaml` (set `domain`, `prompt`, `mode`,
   `scope_hint`, `gcs_uri`, `bq_dataset`, `catalog_queries`,
   `expected_categories`, `expected_terms`).
2. Optional: write a seed script under `test_fixtures/` to populate
   the BigQuery dataset + GCS docs the YAML references.
3. Optional: add an entry to `STARTER_GLOSSARIES` in
   `eval/run_eval.py` so extend-mode scenarios auto-create a partial
   starter glossary for the new domain. Without this, only the two
   "new glossary" scenarios will run for that domain.

Re-run `python -m eval.run_eval` — the new domain appears in the same
report alongside the existing ones.

## Adding a new scenario shape

Edit `_scenarios_for_domain` in `eval/run_eval.py` and append a dict:

```python
{
    "id": f"{domain}/5-my-thing",
    "label": f"[{domain}] What I'm testing",
    "domain": domain,
    "mode": "new",                              # or "extend" / "extend-terms-only"
    "query": "Recommend a glossary for ...",
    "scope_hint": scope_hint,
    "catalog_queries": catalog_queries,
    "gcs_uri": gcs_uri or None,
    "glossary_id": starter_id or None,
    "golden": domain,
}
```

Because `_scenarios_for_domain` runs per golden set, your new scenario
automatically runs for every domain.
