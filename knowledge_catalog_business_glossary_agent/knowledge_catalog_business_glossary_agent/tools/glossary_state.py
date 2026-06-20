"""Existing-glossary reader + embedding-based dedup helpers.

Powers the *extend existing glossary* flow (PRD §3 V2, mode = "extend"):

1. ``get_existing_glossary_state`` pulls categories + terms (with their
   descriptions) so the ontology recommender can avoid re-proposing
   anything that already exists.
2. ``find_similar_existing_terms`` runs cosine similarity between a
   candidate term and the existing terms; the agent uses this to either
   skip a near-duplicate or surface it to the steward as an "alias of
   existing term X" merge suggestion.
3. ``compute_glossary_link_index`` lists EntryLinks already attached to
   each existing term so the link recommender can skip duplicates.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

from ..config import get_dedup_cosine_threshold, get_default_location
from .embeddings import cosine_similarity, embed_one, embed_texts, term_text
from .entry_links import list_entry_links_for_term
from .glossary_crud import (
    get_glossary,
    list_glossary_categories,
    list_glossary_terms,
)

logger = logging.getLogger(__name__)


def _strip_resource_id(name: str) -> str:
  """Returns just the resource id from a fully-qualified resource name."""
  return name.rsplit("/", 1)[-1] if name else ""


def get_existing_glossary_state(
    glossary_id: str,
    location: Optional[str] = None,
) -> Dict:
  """Fetches a glossary's current categories + terms.

  Returns:
      ``{
        "glossary": { "id": ..., "display_name": ..., "description": ... },
        "categories": [
            { "id": ..., "display_name": ..., "description": ... },
            ...
        ],
        "terms": [
            { "id": ..., "display_name": ..., "description": ...,
              "category_id": ... | None },
            ...
        ],
        "location": "<dataplex location>"
      }``
  """
  loc = location or get_default_location()
  glossary = get_glossary(glossary_id, location=loc)
  if "error" in glossary:
    return {
        "error": glossary["error"],
        "details": glossary.get("details"),
    }

  cats_resp = list_glossary_categories(glossary_id, location=loc)
  if "error" in cats_resp:
    logger.warning(
        "Could not list categories for glossary %s: %s",
        glossary_id,
        cats_resp.get("error"),
    )
    cats_resp = {"categories": []}
  terms_resp = list_glossary_terms(glossary_id, location=loc)
  if "error" in terms_resp:
    logger.warning(
        "Could not list terms for glossary %s: %s",
        glossary_id,
        terms_resp.get("error"),
    )
    terms_resp = {"terms": []}

  categories: List[Dict] = []
  for c in cats_resp.get("categories", []) or []:
    name = c.get("name", "")
    categories.append({
        "id": _strip_resource_id(name),
        "display_name": c.get("displayName", ""),
        "description": c.get("description", ""),
        "parent": c.get("parent", ""),
    })

  terms: List[Dict] = []
  for t in terms_resp.get("terms", []) or []:
    name = t.get("name", "")
    parent = t.get("parent", "") or ""
    cat_id = (
        parent.rsplit("/", 1)[-1] if "/categories/" in parent else None
    )
    terms.append({
        "id": _strip_resource_id(name),
        "display_name": t.get("displayName", ""),
        "description": t.get("description", ""),
        "category_id": cat_id,
    })

  return {
      "glossary": {
          "id": glossary_id,
          "display_name": glossary.get("displayName", ""),
          "description": glossary.get("description", ""),
      },
      "categories": categories,
      "terms": terms,
      "location": loc,
  }


def find_similar_existing_terms(
    candidate_display_name: str,
    candidate_description: str,
    existing_terms: Sequence[Dict],
    *,
    top_k: int = 3,
    min_cosine: Optional[float] = None,
) -> Dict:
  """For one candidate term, returns the closest existing terms by cosine.

  ``existing_terms`` must be the ``terms`` list from
  ``get_existing_glossary_state``.

  Returns:
      ``{
        "matches": [
            { "id": "...", "display_name": "...",
              "cosine": 0.xx, "is_duplicate": bool },
            ...
        ],
        "is_duplicate": bool,         # True if any match >= dedup threshold
        "best_match_id": str | None,
      }``
  """
  if not existing_terms:
    return {"matches": [], "is_duplicate": False, "best_match_id": None}

  dedup_t = (
      min_cosine if min_cosine is not None else get_dedup_cosine_threshold()
  )

  candidate_vec = embed_one(
      term_text(candidate_display_name, candidate_description)
  )
  existing_texts = [
      term_text(t.get("display_name", ""), t.get("description", ""))
      for t in existing_terms
  ]
  existing_vecs = embed_texts(existing_texts)

  scored: list[tuple[float, Dict]] = []
  for vec, term in zip(existing_vecs, existing_terms):
    if not vec:
      continue
    s = cosine_similarity(candidate_vec, vec)
    scored.append((s, term))
  scored.sort(key=lambda x: x[0], reverse=True)
  top = scored[:top_k]

  matches = [
      {
          "id": term["id"],
          "display_name": term.get("display_name", ""),
          "cosine": round(score, 4),
          "is_duplicate": score >= dedup_t,
      }
      for score, term in top
  ]
  is_dup = any(m["is_duplicate"] for m in matches)
  best = matches[0]["id"] if matches else None
  return {
      "matches": matches,
      "is_duplicate": is_dup,
      "best_match_id": best,
  }


def find_similar_existing_terms_bulk(
    candidates: Sequence[Dict],
    glossary_id: str,
    location: Optional[str] = None,
    *,
    top_k: int = 3,
    min_cosine: Optional[float] = None,
) -> Dict:
  """Batched version of ``find_similar_existing_terms``.

  Args:
      candidates: list of ``{display_name, description, id (optional)}``.
      glossary_id: which glossary to compare against.
      location: dataplex location for the glossary.

  Returns:
      ``{
        "results": [
            {
              "candidate_id": str,
              "candidate_display_name": str,
              "matches": [...],
              "is_duplicate": bool,
              "best_match_id": str | None,
            },
            ...
        ],
        "glossary_id": "...",
        "existing_term_count": int,
      }``
  """
  state = get_existing_glossary_state(glossary_id, location=location)
  if "error" in state:
    return state

  existing = state["terms"]
  out: List[Dict] = []
  for cand in candidates:
    result = find_similar_existing_terms(
        cand.get("display_name", ""),
        cand.get("description", ""),
        existing,
        top_k=top_k,
        min_cosine=min_cosine,
    )
    out.append({
        "candidate_id": cand.get("id", ""),
        "candidate_display_name": cand.get("display_name", ""),
        "matches": result["matches"],
        "is_duplicate": result["is_duplicate"],
        "best_match_id": result["best_match_id"],
    })

  return {
      "results": out,
      "glossary_id": glossary_id,
      "existing_term_count": len(existing),
  }


def existing_link_targets_for_terms(
    glossary_id: str,
    term_ids: Sequence[str],
    location: Optional[str] = None,
) -> Dict:
  """Returns the set of already-linked entry names per term.

  The link recommender uses this to avoid proposing a link that already
  exists.
  """
  out: Dict[str, List[str]] = {}
  errors: List[Dict] = []
  for tid in term_ids:
    resp = list_entry_links_for_term(
        glossary_id=glossary_id, term_id=tid, location=location
    )
    if "error" in resp:
      errors.append({"term_id": tid, "error": resp.get("error")})
      out[tid] = []
      continue
    targets: List[str] = []
    for link in resp.get("entryLinks", []) or []:
      for ref in link.get("entryReferences", []) or []:
        if ref.get("type") == "TARGET" and ref.get("name"):
          targets.append(ref["name"])
    out[tid] = sorted(set(targets))
  return {"links_by_term": out, "errors": errors}
