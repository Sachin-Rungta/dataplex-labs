"""Single-script eval for the Business Glossary Agent.

Runs four canonical steward scenarios against the synthetic data
seeded by ``test_fixtures/seed_synthetic_data.sh`` and writes ONE
combined Markdown + JSON report scored by an LLM-as-Judge.

Scenarios:
  1. NEW glossary, NO GCS context.
  2. NEW glossary, WITH GCS context.
  3. EXTEND existing glossary (no extra GCS context).
  4. EXTEND existing glossary, WITH GCS context.

Usage (from the agent repo root, with .env exported into the shell):

    python -m eval.run_eval                       # run all 4 scenarios
    python -m eval.run_eval --scenario 2          # one scenario
    python -m eval.run_eval --skip-judges         # P/R only, no LLM rubric (free)
    python -m eval.run_eval --no-seed-glossary    # skip starter-glossary auto-create
    python -m eval.run_eval --query "..." \
        --mode new --gcs-uri gs://...             # freeform query (becomes scenario 0)

For scenarios 3 + 4 the script auto-creates a minimal starter glossary
(``customer-360-glossary`` with one category and two seed terms) the
first time it runs; subsequent runs reuse it. Pass ``--no-seed-glossary``
if you have your own existing glossary you want to test against.

The combined report lands at:
  eval/results/eval-<timestamp>.md   (human)
  eval/results/eval-<timestamp>.json (machine)
  eval/results/latest.md / latest.json (always overwritten)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Allow `python -m eval.run_eval` from the agent dir.
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

from eval.golden_set import GoldenSet, load_golden_set  # noqa: E402
from eval.headless_recommender import (  # noqa: E402
    recommend_links_headless,
    recommend_ontology_headless,
)
from eval.judges import (  # noqa: E402
    judge_category_coherence,
    judge_definition_usefulness,
    judge_term_matches,
)
from eval.metrics import (  # noqa: E402
    aggregate_rubric,
    category_match_rate,
    compute_link_metrics,
    compute_term_metrics,
    find_candidate_term_pairs,
)
from eval.report import render_summary_table, write_reports  # noqa: E402
from knowledge_catalog_business_glossary_agent.config import (  # noqa: E402
    get_classifier_model,
    get_default_location,
)
from knowledge_catalog_business_glossary_agent.tools import (  # noqa: E402
    create_glossary,
    create_glossary_category,
    create_glossary_term,
    get_glossary,
    list_glossary_categories,
    list_glossary_terms,
)

logger = logging.getLogger("eval")


# ---------------------------------------------------------------------------
# Scenario presets
# ---------------------------------------------------------------------------

STARTER_GLOSSARY_ID = "customer-360-glossary"
STARTER_GLOSSARY_DISPLAY = "Customer 360 Glossary"
STARTER_GLOSSARY_DESCRIPTION = (
    "Seed glossary used by the eval harness to test extend-mode flows."
)
STARTER_SEED_CATEGORY = {
    "id": "customer-profile",
    "display_name": "Customer Profile",
    "description": "Identity and structural attributes of a customer.",
}
STARTER_SEED_TERMS = [
    {
        "id": "customer",
        "display_name": "Customer",
        "description": "An individual or entity that has created an account.",
        "category_id": "customer-profile",
    },
    {
        "id": "customer-segment",
        "display_name": "Customer Segment",
        "description": "Go-to-market segment (Enterprise / SMB / Consumer).",
        "category_id": "customer-profile",
    },
]


def _scenarios(project: str) -> List[Dict]:
  gcs_uri = f"gs://{project}-glossary-test/"
  return [
      {
          "id": "1-new-no-gcs",
          "label": "New glossary — NL only (no GCS)",
          "mode": "new",
          "query": (
              f"Recommend a new glossary for our customer-360 domain in"
              f" project {project}. Focus on business concepts, not"
              f" column-name fragments."
          ),
          "scope_hint": "customer 360 subscription",
          "catalog_queries": ["customer_360", "customers system=bigquery"],
          "gcs_uri": None,
          "golden": "customer_360",
      },
      {
          "id": "2-new-with-gcs",
          "label": "New glossary — NL + GCS context",
          "mode": "new",
          "query": (
              f"Recommend a new glossary for our customer-360 domain in"
              f" project {project}, grounded in {gcs_uri}. Focus on"
              f" business concepts, not column-name fragments."
          ),
          "scope_hint": "customer 360 subscription",
          "catalog_queries": ["customer_360", "customers system=bigquery"],
          "gcs_uri": gcs_uri,
          "golden": "customer_360",
      },
      {
          "id": "3-extend-no-gcs",
          "label": "Extend existing glossary — catalog signal only (no GCS)",
          "mode": "extend",
          "query": (
              f"Add new terms and categories to the {STARTER_GLOSSARY_ID}"
              f" glossary using the customer_360 catalog tables only."
              f" Skip duplicates of existing terms."
          ),
          "scope_hint": "customer 360 subscription",
          "catalog_queries": ["customer_360", "customers system=bigquery"],
          "gcs_uri": None,
          "glossary_id": STARTER_GLOSSARY_ID,
          "golden": "customer_360",
      },
      {
          "id": "4-extend-with-gcs",
          "label": "Extend existing glossary — NL + GCS context",
          "mode": "extend",
          "query": (
              f"Add new terms and categories to the {STARTER_GLOSSARY_ID}"
              f" glossary using {gcs_uri} plus the customer_360 catalog."
              f" Skip duplicates of existing terms."
          ),
          "scope_hint": "customer 360 subscription",
          "catalog_queries": ["customer_360", "customers system=bigquery"],
          "gcs_uri": gcs_uri,
          "glossary_id": STARTER_GLOSSARY_ID,
          "golden": "customer_360",
      },
  ]


# ---------------------------------------------------------------------------
# Starter glossary helper (scenarios 3 + 4)
# ---------------------------------------------------------------------------

def ensure_starter_glossary(
    glossary_id: str = STARTER_GLOSSARY_ID,
    location: Optional[str] = None,
) -> Dict:
  """Idempotent: creates a minimal starter glossary if missing.

  Used by extend-mode scenarios so the eval can run end-to-end without
  the steward first manually creating something.
  """
  loc = location or get_default_location()

  existing = get_glossary(glossary_id, location=loc)
  if "error" not in existing:
    return {"created": False, "glossary_id": glossary_id, "location": loc}

  # Create glossary
  g = create_glossary(
      glossary_id=glossary_id,
      display_name=STARTER_GLOSSARY_DISPLAY,
      description=STARTER_GLOSSARY_DESCRIPTION,
      location=loc,
  )
  if "error" in g:
    return {"error": f"failed to create starter glossary: {g}"}

  # Category
  c = create_glossary_category(
      glossary_id=glossary_id,
      category_id=STARTER_SEED_CATEGORY["id"],
      display_name=STARTER_SEED_CATEGORY["display_name"],
      description=STARTER_SEED_CATEGORY["description"],
      location=loc,
  )
  if "error" in c:
    return {"error": f"failed to create starter category: {c}"}

  # Terms
  for t in STARTER_SEED_TERMS:
    r = create_glossary_term(
        glossary_id=glossary_id,
        term_id=t["id"],
        display_name=t["display_name"],
        description=t["description"],
        category_id=t["category_id"],
        location=loc,
    )
    if "error" in r:
      return {"error": f"failed to create starter term {t['id']}: {r}"}

  return {
      "created": True,
      "glossary_id": glossary_id,
      "location": loc,
      "seed_category": STARTER_SEED_CATEGORY["id"],
      "seed_terms": [t["id"] for t in STARTER_SEED_TERMS],
  }


# ---------------------------------------------------------------------------
# Per-scenario runner
# ---------------------------------------------------------------------------

def run_scenario(
    scenario: Dict,
    golden: GoldenSet,
    skip_judges: bool,
) -> Dict:
  logger.info("[scenario %s] %s", scenario["id"], scenario["label"])

  ont = recommend_ontology_headless(
      catalog_queries=scenario["catalog_queries"],
      gcs_uri=scenario.get("gcs_uri"),
      scope_hint=scenario["scope_hint"],
      mode=scenario["mode"],
      glossary_id=scenario.get("glossary_id"),
      glossary_location=scenario.get("glossary_location"),
  )
  if "error" in ont:
    return {**scenario, "error": ont["error"]}

  rec = ont.get("recommendation") or {}
  rec_terms = rec.get("terms", []) or []
  rec_categories = rec.get("categories", []) or []

  # Links
  link_resp = recommend_links_headless(
      terms=rec_terms,
      entries=ont.get("graph_entries") or [],
  )
  proposals = (link_resp.get("recommendation") or {}).get("proposals", []) or []

  # Term-match judge
  golden_terms = [t.model_dump() for t in golden.expected_terms]
  golden_categories = [c.model_dump() for c in golden.expected_categories]
  pairs = find_candidate_term_pairs(rec_terms, golden_terms)
  judge_verdicts: List[Dict] = []
  if pairs and not skip_judges:
    judge_verdicts = judge_term_matches(pairs)

  term_metrics = compute_term_metrics(rec_terms, golden_terms, judge_verdicts, pairs)
  matched_pairs = list(
      zip(term_metrics["matched_rec_indices"], term_metrics["matched_gold_indices"])
  )

  cat_match = category_match_rate(rec_categories, golden_categories)

  # Category coherence
  coh_verdicts: List[Dict] = []
  if not skip_judges and rec_categories:
    payload = []
    for ci, c in enumerate(rec_categories):
      members = [
          t["display_name"] for t in rec_terms if t.get("category_id") == c.get("id")
      ]
      if not members:
        continue
      payload.append({
          "category_index": ci,
          "display_name": c.get("display_name", ""),
          "description": c.get("description", ""),
          "member_terms": members,
      })
    if payload:
      coh_verdicts = judge_category_coherence(payload)
  coherence_agg = aggregate_rubric(coh_verdicts)

  # Definition usefulness
  def_verdicts: List[Dict] = []
  if not skip_judges and rec_terms:
    def_verdicts = judge_definition_usefulness(
        [
            {
                "term_index": ti,
                "display_name": t.get("display_name", ""),
                "description": t.get("description", ""),
            }
            for ti, t in enumerate(rec_terms)
        ],
        domain_context=scenario["scope_hint"],
    )
  definition_agg = aggregate_rubric(def_verdicts)

  link_metrics = compute_link_metrics(
      proposals, golden_terms, matched_pairs, rec_terms
  )

  return {
      "id": scenario["id"],
      "label": scenario["label"],
      "mode": scenario["mode"],
      "query": scenario["query"],
      "gcs_uri": scenario.get("gcs_uri"),
      "glossary_id": scenario.get("glossary_id"),
      "graph_stats": ont.get("graph_stats"),
      "embed_stats": ont.get("embed_stats"),
      "recommendation": rec,
      "link_proposals": proposals,
      "term_metrics": term_metrics,
      "category_match": cat_match,
      "category_coherence": coherence_agg,
      "category_coherence_verdicts": coh_verdicts,
      "definition_usefulness": definition_agg,
      "definition_verdicts": def_verdicts,
      "link_metrics": link_metrics,
      "judge_pairs": pairs,
      "judge_verdicts": judge_verdicts,
  }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
  parser = argparse.ArgumentParser(
      description=(
          "Run the 4-scenario glossary-agent eval and emit one combined"
          " Markdown + JSON report."
      ),
  )
  parser.add_argument(
      "--project",
      default=os.environ.get("GOOGLE_CLOUD_PROJECT"),
      help="GCP project (defaults to $GOOGLE_CLOUD_PROJECT).",
  )
  parser.add_argument(
      "--scenario",
      help="Run only the named scenario id (1, 2, 3, or 4). Default: all.",
  )
  parser.add_argument(
      "--golden-root",
      default=str(REPO_ROOT / "eval" / "golden"),
      help="Directory containing <domain>/expected.yaml golden sets.",
  )
  parser.add_argument(
      "--output-dir",
      default=str(REPO_ROOT / "eval" / "results"),
      help="Where to write the combined report.",
  )
  parser.add_argument(
      "--skip-judges",
      action="store_true",
      help="Skip LLM-as-Judge calls; only structural metrics. Free + fast.",
  )
  parser.add_argument(
      "--no-seed-glossary",
      action="store_true",
      help=(
          "Don't auto-create the starter glossary for extend scenarios."
          " Use this if you already have your own existing glossary"
          " populated via the agent."
      ),
  )

  # Freeform single-shot mode.
  parser.add_argument("--query", help="Freeform NL query (becomes scenario 0).")
  parser.add_argument("--mode", choices=["new", "extend", "extend-terms-only"])
  parser.add_argument("--gcs-uri", default=None)
  parser.add_argument("--scope-hint", default="custom")
  parser.add_argument("--catalog-query", action="append", default=None)
  parser.add_argument("--glossary-id", default=None)
  parser.add_argument("--golden-domain", default="customer_360")

  parser.add_argument("-v", "--verbose", action="store_true")

  args = parser.parse_args()

  logging.basicConfig(
      level=logging.DEBUG if args.verbose else logging.INFO,
      format="%(asctime)s %(levelname)s %(name)s: %(message)s",
  )

  if not args.project:
    print("Set --project or GOOGLE_CLOUD_PROJECT.", file=sys.stderr)
    return 2

  # Build the scenario list.
  if args.query:
    if not args.mode:
      print("--query requires --mode (new / extend / extend-terms-only).", file=sys.stderr)
      return 2
    scenarios = [{
        "id": "0-freeform",
        "label": "Freeform query",
        "mode": args.mode,
        "query": args.query,
        "scope_hint": args.scope_hint,
        "catalog_queries": args.catalog_query or [args.scope_hint],
        "gcs_uri": args.gcs_uri,
        "glossary_id": args.glossary_id,
        "golden": args.golden_domain,
    }]
  else:
    scenarios = _scenarios(args.project)
    if args.scenario:
      pick = args.scenario
      scenarios = [
          s for s in scenarios
          if s["id"].startswith(pick) or s["id"] == pick
      ]
      if not scenarios:
        print(
            f"--scenario={args.scenario} did not match any preset"
            " (try 1, 2, 3, or 4).",
            file=sys.stderr,
        )
        return 2

  # Load golden sets we'll need.
  needed_goldens = sorted({s["golden"] for s in scenarios})
  goldens: Dict[str, GoldenSet] = {}
  for d in needed_goldens:
    p = Path(args.golden_root) / d / "expected.yaml"
    if not p.exists():
      print(f"Golden YAML not found: {p}", file=sys.stderr)
      return 2
    goldens[d] = load_golden_set(p, substitutions={"PROJECT": args.project})

  # Seed starter glossary if any extend-mode scenario needs it.
  starter_info: Optional[Dict] = None
  needs_starter = any(
      s["mode"] in ("extend", "extend-terms-only")
      and s.get("glossary_id") == STARTER_GLOSSARY_ID
      for s in scenarios
  )
  if needs_starter and not args.no_seed_glossary:
    starter_info = ensure_starter_glossary()
    if starter_info.get("error"):
      print(
          f"Starter-glossary setup failed: {starter_info['error']}",
          file=sys.stderr,
      )
      print(
          "Re-run with --no-seed-glossary after creating one manually,"
          " or fix permissions and retry.",
          file=sys.stderr,
      )
      return 3
    logger.info("Starter glossary: %s", starter_info)

  # Run each scenario.
  results: Dict[str, Dict] = {}
  for s in scenarios:
    try:
      r = run_scenario(s, goldens[s["golden"]], skip_judges=args.skip_judges)
    except Exception as e:
      logger.exception("scenario %s failed", s["id"])
      r = {"id": s["id"], "label": s["label"], "error": str(e)}
    results[s["id"]] = r

  # Write reports.
  meta = {
      "generated_at": datetime.now(timezone.utc).isoformat(),
      "project": args.project,
      "model": get_classifier_model(),
      "skip_judges": args.skip_judges,
      "scenarios": [s["id"] for s in scenarios],
      "starter_glossary": starter_info,
  }
  paths = write_reports(results, args.output_dir, meta)

  print()
  print(f"Wrote {paths['md']}")
  print(f"Wrote {paths['json']}")
  print(f"Latest: {paths['latest_md']}")
  print()
  print(render_summary_table(results))
  return 0


if __name__ == "__main__":
  sys.exit(main())
