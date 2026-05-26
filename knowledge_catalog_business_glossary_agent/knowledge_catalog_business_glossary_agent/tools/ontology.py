"""Deterministic scorers used to support LLM ontology recommendations.

The LLM is the primary decision-maker, but these tools give it cheap,
explainable signals it can cite back to the steward.
"""

from typing import Dict, List

from .context_graph import shared_concepts, tokenize


def score_term_candidates(graph: Dict, top_k: int = 25) -> Dict:
  """Promotes graph concepts to ranked term candidates.

  Scoring favors concepts that appear across multiple sources (entries OR
  documents) over concepts that are merely frequent in a single source.
  """
  candidates: List[Dict] = []
  for c in graph.get("concepts", []):
    sources = c.get("sources", [])
    breadth = len(set(sources))
    score = c["frequency"] * (1 + 0.5 * breadth)
    candidates.append({
        "term": c["name"],
        "score": round(score, 2),
        "frequency": c["frequency"],
        "source_breadth": breadth,
        "example_sources": sources[:3],
    })
  candidates.sort(key=lambda x: x["score"], reverse=True)
  return {"candidates": candidates[:top_k]}


def suggest_link_candidates(
    term: str,
    term_description: str,
    entries: List[Dict],
    top_k: int = 10,
) -> Dict:
  """Suggests entries most likely to be linkable to a given term.

  Compares tokens of (term + description) against tokens of each entry's
  (display_name + resource_id + description). Returns a ranked list with a
  proposed relationship string the steward can override.
  """
  term_tokens = set(tokenize(f"{term} {term_description}"))
  if not term_tokens:
    return {"candidates": []}

  scored: List[Dict] = []
  for entry in entries:
    entry_text = " ".join(
        str(entry.get(k, ""))
        for k in ("display_name", "resource_id", "description")
    )
    entry_tokens = set(tokenize(entry_text))
    shared = shared_concepts(term_tokens, entry_tokens)
    if not shared:
      continue
    overlap = len(shared)
    name_hit = term.lower() in (entry.get("display_name") or "").lower()
    score = overlap + (3 if name_hit else 0)
    relationship = "synonym" if name_hit and overlap >= 2 else "definition"
    scored.append({
        "entry_name": entry.get("entry_name"),
        "display_name": entry.get("display_name"),
        "score": score,
        "shared_concepts": shared,
        "suggested_relationship": relationship,
    })
  scored.sort(key=lambda x: x["score"], reverse=True)
  return {"candidates": scored[:top_k]}
