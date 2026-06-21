# Evaluation Harness

One Python script. Four scenarios. One combined report.

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
"starter glossary" (`customer-360-glossary` with two categories —
*Customer Profile* and *Subscription* — and six seed terms drawn
from the golden set: *Customer*, *Customer Segment*, *Customer
Lifetime Value*, *Subscription Account*, *Plan Tier*, *Monthly
Recurring Revenue*) the first time you run it, so the agent has
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

# All four scenarios with LLM-as-Judge:
python -m eval.run_eval

# Just one scenario:
python -m eval.run_eval --scenario 2

# Fast / free run — structural metrics only, no LLM judging:
python -m eval.run_eval --skip-judges

# Freeform: bring your own query
python -m eval.run_eval \
    --query "Recommend a glossary for our marketing domain" \
    --mode new --scope-hint "marketing campaign lead funnel"
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

## Adding a new scenario

Open `eval/run_eval.py` and append a dict to `_scenarios()`:

```python
{
    "id": "5-my-thing",
    "label": "What I'm testing",
    "mode": "new",                              # or "extend" / "extend-terms-only"
    "query": "Recommend a glossary for ...",
    "scope_hint": "...",
    "catalog_queries": ["..."],
    "gcs_uri": "gs://..." or None,
    "glossary_id": "..." or None,
    "golden": "customer_360",                    # which golden set to score against
},
```

Re-run `python -m eval.run_eval` — the new scenario joins the others
in the same report.
