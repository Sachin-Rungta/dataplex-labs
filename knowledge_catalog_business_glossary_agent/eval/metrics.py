"""Compute eval metrics from recommendations + golden sets + judge outputs.

Term metrics (from §6.3 of DESIGN.md):
  * precision = TP / (TP + FP)
  * recall    = TP / (TP + FN)
  * F1        = 2 · P · R / (P + R)
  * P@K       = TP within top-K recommendations / K

Link metrics:
  * link_recall = fraction of golden must_link tuples surfaced by the
                  agent (matched by term name + entry_suffix)
  * relationship_type_accuracy = of correctly-recalled links, fraction
                  where the predicted relationship matches the golden one

Category metrics:
  * mean_coherence_score (from judge_category_coherence; 1-5)
  * frac_coherent (score >= 4)

Definition metrics:
  * mean_definition_score (from judge_definition_usefulness; 1-5)
  * frac_useful (score >= 4)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from knowledge_catalog_business_glossary_agent.tools.embeddings import (
    cosine_similarity,
    embed_one,
    embed_texts,
    term_text,
)


# ---------------------------------------------------------------------------
# Term matching
# ---------------------------------------------------------------------------

def find_candidate_term_pairs(
    recommended: List[Dict],
    golden: List[Dict],
    *,
    cosine_threshold: float = 0.55,
) -> List[Dict]:
  """Builds candidate (recommended, golden) pairs for the LLM judge.

  We over-generate candidates (any pair with embedding cosine above
  ``cosine_threshold``) and let the judge filter. That keeps the
  precision/recall numerator honest — a tighter cosine cutoff would
  hide false positives the judge would correctly reject.

  Returns list of dicts with ``pair_index`` (matches judge input),
  ``rec_index``, ``gold_index``, ``cosine``, ``recommended`` (dict),
  ``golden`` (dict).
  """
  if not recommended or not golden:
    return []

  # Compute one embedding per recommended + one per golden display_name.
  rec_vecs = embed_texts([term_text(r["display_name"], r.get("description", "")) for r in recommended])
  gold_vecs = embed_texts([term_text(g["display_name"], g.get("description", "")) for g in golden])

  pairs: List[Dict] = []
  idx = 0
  for ri, rv in enumerate(rec_vecs):
    for gi, gv in enumerate(gold_vecs):
      if not rv or not gv:
        continue
      c = cosine_similarity(rv, gv)
      # Also include if name OR any alias is exact-substring (case-insensitive).
      gname = (golden[gi]["display_name"] or "").lower()
      rname = (recommended[ri]["display_name"] or "").lower()
      aliases = [a.lower() for a in (golden[gi].get("aliases", []) or [])]
      name_hit = rname == gname or rname in aliases or gname in rname
      if c >= cosine_threshold or name_hit:
        pairs.append({
            "pair_index": idx,
            "rec_index": ri,
            "gold_index": gi,
            "cosine": round(c, 4),
            "recommended": {
                "display_name": recommended[ri]["display_name"],
                "description": recommended[ri].get("description", ""),
            },
            "golden": {
                "display_name": golden[gi]["display_name"],
                "description": golden[gi].get("description", ""),
                "aliases": golden[gi].get("aliases", []),
            },
        })
        idx += 1
  return pairs


def compute_term_metrics(
    recommended: List[Dict],
    golden: List[Dict],
    judge_verdicts: List[Dict],
    candidate_pairs: List[Dict],
    *,
    k_values: Tuple[int, ...] = (5, 10, 20),
) -> Dict:
  """From candidate pairs + judge verdicts, computes term P/R/F1/P@K.

  A recommended term is a TP if any of its candidate pairs has
  ``matches=True`` from the judge.
  A golden term is recalled if any of its candidate pairs has
  ``matches=True`` from the judge.
  """
  # Map verdicts back to pairs.
  matched_by_pair = {v["pair_index"]: v["matches"] for v in judge_verdicts}
  matched_rec: Set[int] = set()
  matched_gold: Set[int] = set()
  matches: List[Tuple[int, int]] = []  # (rec_index, gold_index)
  for p in candidate_pairs:
    if matched_by_pair.get(p["pair_index"]) is True:
      matched_rec.add(p["rec_index"])
      matched_gold.add(p["gold_index"])
      matches.append((p["rec_index"], p["gold_index"]))

  n_rec = len(recommended)
  n_gold = len(golden)
  tp = len(matched_rec)
  fp = n_rec - tp
  fn = n_gold - len(matched_gold)
  precision = tp / max(1, n_rec)
  recall = tp / max(1, n_gold)
  f1 = (
      0.0
      if (precision + recall) == 0
      else 2 * precision * recall / (precision + recall)
  )

  p_at_k: Dict[int, float] = {}
  for k in k_values:
    top = list(range(min(k, n_rec)))
    tp_in_top = sum(1 for i in top if i in matched_rec)
    p_at_k[k] = tp_in_top / max(1, len(top))

  return {
      "n_recommended": n_rec,
      "n_golden": n_gold,
      "true_positives": tp,
      "false_positives": fp,
      "false_negatives": fn,
      "precision": round(precision, 4),
      "recall": round(recall, 4),
      "f1": round(f1, 4),
      "p_at_k": {k: round(v, 4) for k, v in p_at_k.items()},
      "matched_rec_indices": sorted(matched_rec),
      "matched_gold_indices": sorted(matched_gold),
      "false_positive_terms": [
          recommended[i]["display_name"] for i in range(n_rec) if i not in matched_rec
      ],
      "false_negative_terms": [
          golden[i]["display_name"] for i in range(n_gold) if i not in matched_gold
      ],
  }


# ---------------------------------------------------------------------------
# Link metrics
# ---------------------------------------------------------------------------

def _entry_matches_suffix(entry_name: str, suffix: str) -> bool:
  """Match by tail substring so we tolerate different project/location prefixes."""
  if not entry_name or not suffix:
    return False
  return entry_name.endswith(suffix) or suffix in entry_name


def compute_link_metrics(
    proposals: List[Dict],
    golden: List[Dict],
    matched_pairs: List[Tuple[int, int]],
    recommended_terms: List[Dict],
) -> Dict:
  """For each golden ``must_link``, check whether the agent proposed it.

  A golden link is recalled if there exists an agent proposal where:
    * the proposal's term_display_name matches the golden term (or its
      golden term was matched to one of the recommended terms);
    * the proposal's target_entry_name endswith / contains the
      golden entry_suffix.
  """
  expected: List[Dict] = []
  for gi, g in enumerate(golden):
    for ml in g.get("must_link", []) or []:
      expected.append({
          "golden_index": gi,
          "golden_display_name": g["display_name"],
          "entry_suffix": ml["entry_suffix"],
          "relationship": ml["relationship"],
      })

  if not expected:
    return {
        "n_expected_links": 0,
        "n_proposed_links": len(proposals),
        "link_recall": None,
        "relationship_type_accuracy": None,
        "missing_links": [],
      }

  # Helper: map golden_index → matched recommended term display name(s).
  gold_to_rec: Dict[int, Set[str]] = {}
  for ri, gi in matched_pairs:
    gold_to_rec.setdefault(gi, set()).add(recommended_terms[ri]["display_name"])

  recalled = 0
  relationship_hits = 0
  relationship_total = 0
  missing: List[Dict] = []

  for exp in expected:
    candidate_term_names = gold_to_rec.get(exp["golden_index"], set()) | {
        exp["golden_display_name"]
    }
    hit = None
    for p in proposals:
      if p.get("term_display_name") not in candidate_term_names:
        continue
      if not _entry_matches_suffix(p.get("target_entry_name", ""), exp["entry_suffix"]):
        continue
      hit = p
      break
    if hit:
      recalled += 1
      relationship_total += 1
      if hit.get("relationship") == exp["relationship"]:
        relationship_hits += 1
    else:
      missing.append(exp)

  return {
      "n_expected_links": len(expected),
      "n_proposed_links": len(proposals),
      "link_recall": round(recalled / max(1, len(expected)), 4),
      "relationship_type_accuracy": (
          round(relationship_hits / relationship_total, 4)
          if relationship_total
          else None
      ),
      "missing_links": missing,
  }


# ---------------------------------------------------------------------------
# Category + definition rubric aggregates
# ---------------------------------------------------------------------------

def aggregate_rubric(verdicts: List[Dict]) -> Dict:
  """Mean + ≥4 fraction. Drops any verdict where score == 0 (judge error)."""
  scores = [v["score"] for v in verdicts if v.get("score", 0) > 0]
  if not scores:
    return {"n": 0, "mean": None, "frac_ge_4": None}
  return {
      "n": len(scores),
      "mean": round(sum(scores) / len(scores), 3),
      "frac_ge_4": round(sum(1 for s in scores if s >= 4) / len(scores), 3),
  }


# ---------------------------------------------------------------------------
# Category matching (lightweight — no LLM judge needed for the headline)
# ---------------------------------------------------------------------------

def category_match_rate(
    recommended_categories: List[Dict],
    golden_categories: List[Dict],
    *,
    cosine_threshold: float = 0.65,
) -> Dict:
  """Counts how many golden categories were surfaced (by name cosine or alias)."""
  if not golden_categories:
    return {"n_golden": 0, "n_matched": 0, "match_rate": None}
  if not recommended_categories:
    return {
        "n_golden": len(golden_categories),
        "n_matched": 0,
        "match_rate": 0.0,
        "missing": [g["display_name"] for g in golden_categories],
    }

  rec_vecs = [embed_one(r["display_name"]) for r in recommended_categories]
  matched: Set[int] = set()
  for gi, g in enumerate(golden_categories):
    gv = embed_one(g["display_name"])
    if not gv:
      continue
    aliases_lc = {a.lower() for a in (g.get("aliases") or [])}
    aliases_lc.add(g["display_name"].lower())
    found = False
    for ri, rv in enumerate(rec_vecs):
      if not rv:
        continue
      if recommended_categories[ri]["display_name"].lower() in aliases_lc:
        found = True
        break
      if cosine_similarity(gv, rv) >= cosine_threshold:
        found = True
        break
    if found:
      matched.add(gi)

  return {
      "n_golden": len(golden_categories),
      "n_matched": len(matched),
      "match_rate": round(len(matched) / len(golden_categories), 4),
      "missing": [
          g["display_name"]
          for gi, g in enumerate(golden_categories) if gi not in matched
      ],
  }
