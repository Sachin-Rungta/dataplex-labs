"""Semantic scorers + cluster-to-category seeds for the ontology + link
recommenders. This is the V2 spine: embeddings drive everything that
mattered to glossary quality.

Public surface used by the sub-agents (kept thin on purpose so the
LLM prompts can call these by name):

  * ``embed_context_graph`` — warms the embedding cache for every concept
    and entry in the graph; returns a stats dict.
  * ``score_term_candidates_semantic`` — re-ranks candidate terms using
    cosine to a "domain centroid" + lexical breadth.
  * ``cluster_concepts_for_categories`` — runs agglomerative clustering
    on concept embeddings and surfaces ranked exemplars per cluster.
  * ``suggest_link_candidates_semantic`` — for a single term, returns the
    catalog entries most similar to it by cosine, ready to feed the LLM
    relationship classifier.
  * ``suggest_link_candidates_bulk`` — vectorized version: one matrix
    multiplication per turn over all terms × all entries.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

from ..config import (
    get_link_cosine_threshold,
    get_max_terms,
)
from .clustering import cluster_concepts
from .embeddings import (
    concept_text,
    cosine_similarity,
    embed_one,
    embed_texts,
    entry_text,
    term_text,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedding warm-up
# ---------------------------------------------------------------------------

def embed_context_graph(graph: Dict) -> Dict:
  """Warms the embedding cache for every concept name + entry in the graph.

  After this call, downstream tools (clustering, semantic scoring, link
  candidate ranking) read from the cache for free. The graph itself is
  not mutated — vectors stay in the cache.
  """
  if "error" in graph:
    return {"error": graph["error"]}

  # Build neighbor lookup so concept embeddings include co-occurrence
  # context (helps single-token concepts like "customer" embed more
  # meaningfully).
  neighbors_by_concept: dict[str, list[str]] = {}
  for edge in graph.get("edges", []) or []:
    a, b, w = edge.get("source", ""), edge.get("target", ""), edge.get("weight", 0)
    if not a or not b:
      continue
    neighbors_by_concept.setdefault(a, []).append((b, w))
    neighbors_by_concept.setdefault(b, []).append((a, w))

  def _top_neighbors(name: str) -> List[str]:
    pairs = neighbors_by_concept.get(name, [])
    pairs.sort(key=lambda p: p[1], reverse=True)
    return [n for n, _ in pairs[:5]]

  concept_texts = [
      concept_text(c.get("name", ""), _top_neighbors(c.get("name", "")))
      for c in graph.get("concepts", []) or []
  ]
  entry_texts = [entry_text(e) for e in graph.get("entries", []) or []]

  embed_texts(concept_texts)
  embed_texts(entry_texts)

  return {
      "concepts_embedded": len(concept_texts),
      "entries_embedded": len(entry_texts),
  }


# ---------------------------------------------------------------------------
# Term candidate scoring
# ---------------------------------------------------------------------------

def _domain_centroid(graph: Dict, scope_hint: Optional[str] = None) -> List[float]:
  """Returns a single embedding vector that represents 'this domain'.

  The centroid is the mean of:
    * the ``scope_hint`` embedding (if provided), weighted 2x to bias
      towards what the steward asked for; plus
    * the top entry embeddings (top 25 by frequency proxy).
  """
  vectors: list[List[float]] = []
  weights: list[float] = []

  if scope_hint and scope_hint.strip():
    vectors.append(embed_one(scope_hint.strip()))
    weights.append(2.0)

  for entry in (graph.get("entries", []) or [])[:25]:
    vectors.append(embed_one(entry_text(entry)))
    weights.append(1.0)

  if not vectors:
    return []
  dim = len(vectors[0])
  acc = [0.0] * dim
  total = 0.0
  for vec, w in zip(vectors, weights):
    if not vec:
      continue
    for i in range(dim):
      acc[i] += vec[i] * w
    total += w
  if total == 0:
    return []
  return [x / total for x in acc]


def score_term_candidates_semantic(
    graph: Dict,
    *,
    scope_hint: Optional[str] = None,
    top_k: Optional[int] = None,
) -> Dict:
  """Re-ranks context-graph concepts by relevance to the domain centroid.

  The score fuses:
    * lexical: ``frequency * (1 + 0.5 * source_breadth)`` (V1 signal)
    * semantic: cosine(concept_embedding, domain_centroid)
    * weighted sum: ``0.4 * lexical_normalized + 0.6 * cosine``

  Returns the same shape as the original V1 ``score_term_candidates`` so
  the prompts can stay compatible.
  """
  concepts = graph.get("concepts", []) or []
  if not concepts:
    return {"candidates": []}

  cap = top_k if top_k is not None else get_max_terms()

  centroid = _domain_centroid(graph, scope_hint)
  texts = [
      concept_text(c.get("name", ""), [
          e.get("target") if e.get("source") == c.get("name") else e.get("source")
          for e in (graph.get("edges", []) or [])[:200]
          if c.get("name") in (e.get("source"), e.get("target"))
      ])
      for c in concepts
  ]
  vectors = embed_texts(texts)

  # Normalize lexical score to [0, 1] so weights make sense.
  raw_lex = []
  for c in concepts:
    freq = int(c.get("frequency", 0) or 0)
    breadth = len(set(c.get("sources", []) or []))
    raw_lex.append(freq * (1.0 + 0.5 * breadth))
  lex_max = max(raw_lex) if raw_lex else 1.0
  lex_max = lex_max or 1.0

  scored: List[Dict] = []
  for c, vec, lex in zip(concepts, vectors, raw_lex):
    cos = cosine_similarity(vec, centroid) if vec and centroid else 0.0
    norm_lex = lex / lex_max
    score = 0.4 * norm_lex + 0.6 * cos
    scored.append({
        "term": c.get("name", ""),
        "score": round(score, 4),
        "cosine_to_domain": round(cos, 4),
        "lexical_score": round(lex, 2),
        "frequency": int(c.get("frequency", 0) or 0),
        "source_breadth": len(set(c.get("sources", []) or [])),
        "example_sources": (c.get("sources", []) or [])[:3],
    })

  scored.sort(key=lambda x: x["score"], reverse=True)
  return {"candidates": scored[:cap]}


# ---------------------------------------------------------------------------
# Category seeds
# ---------------------------------------------------------------------------

def cluster_concepts_for_categories(
    graph: Dict,
    *,
    distance_threshold: Optional[float] = None,
    min_cluster_size: Optional[int] = None,
    max_clusters: Optional[int] = None,
) -> Dict:
  """Runs clustering over the graph's concepts.

  Returns:
      ``{
        "clusters": [
            {
              "cluster_id": "c0",
              "exemplars": [...],
              "concepts": [...],
              "size": 4,
              "total_frequency": 17,
              "suggested_category_id": "<slug-of-top-exemplar>",
            },
            ...
        ],
        "miscellaneous": [...],
      }``
  """
  concepts = graph.get("concepts", []) or []
  if not concepts:
    return {"clusters": [], "miscellaneous": []}

  # Build neighbor lookup once so each concept text matches what was used
  # in embed_context_graph (cache hit).
  neighbors: dict[str, list[str]] = {}
  for edge in graph.get("edges", []) or []:
    a, b, w = edge.get("source", ""), edge.get("target", ""), edge.get("weight", 0)
    if not a or not b:
      continue
    neighbors.setdefault(a, []).append((b, w))
    neighbors.setdefault(b, []).append((a, w))

  enriched_concepts = []
  for c in concepts:
    name = c.get("name", "")
    pairs = neighbors.get(name, [])
    pairs.sort(key=lambda p: p[1], reverse=True)
    enriched_concepts.append({
        "name": name,
        "frequency": int(c.get("frequency", 0) or 0),
        "neighbors": [n for n, _ in pairs[:5]],
    })

  result = cluster_concepts(
      enriched_concepts,
      distance_threshold=distance_threshold,
      min_cluster_size=min_cluster_size,
      max_clusters=max_clusters,
  )

  # Suggest a slug per cluster so the LLM has a starting point if it
  # wants to keep the same id.
  for cl in result.get("clusters", []):
    top = (cl.get("exemplars") or [""])[0]
    cl["suggested_category_id"] = _slug(top) or cl["cluster_id"]

  return result


def _slug(s: str) -> str:
  import re

  return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


# ---------------------------------------------------------------------------
# Link candidate ranking
# ---------------------------------------------------------------------------

def suggest_link_candidates_semantic(
    term_display_name: str,
    term_description: str,
    entries: Sequence[Dict],
    *,
    top_k: int = 15,
    min_cosine: Optional[float] = None,
) -> Dict:
  """Returns ``top_k`` entries most similar to the term by cosine.

  Output is shaped to feed directly into the LLM relationship classifier.
  """
  if not entries:
    return {"candidates": []}

  threshold = (
      min_cosine if min_cosine is not None else get_link_cosine_threshold()
  )

  term_vec = embed_one(term_text(term_display_name, term_description))
  entry_vecs = embed_texts([entry_text(e) for e in entries])

  scored: List[Dict] = []
  for entry, vec in zip(entries, entry_vecs):
    cos = cosine_similarity(term_vec, vec) if vec else 0.0
    if cos < threshold:
      continue
    scored.append({
        "entry_name": entry.get("entry_name"),
        "display_name": entry.get("display_name"),
        "description": entry.get("description"),
        "system": entry.get("system"),
        "resource_id": entry.get("resource_id"),
        "cosine": round(cos, 4),
    })

  scored.sort(key=lambda x: x["cosine"], reverse=True)
  return {"candidates": scored[:top_k]}


def suggest_link_candidates_bulk(
    terms: Sequence[Dict],
    entries: Sequence[Dict],
    *,
    top_k_per_term: int = 8,
    min_cosine: Optional[float] = None,
) -> Dict:
  """Computes cosine for every (term, entry) pair in one pass.

  Args:
      terms: list of ``{id, display_name, description}``.
      entries: list of catalog entries.

  Returns:
      ``{
        "candidates": [
            {
              "term_id": ...,
              "term_display_name": ...,
              "term_description": ...,
              "entries": [
                  {
                    "entry_name": ...,
                    "display_name": ...,
                    "description": ...,
                    "cosine": ...,
                  },
                  ...
              ],
            },
            ...
        ],
      }``
  """
  if not terms or not entries:
    return {"candidates": []}

  threshold = (
      min_cosine if min_cosine is not None else get_link_cosine_threshold()
  )

  term_vecs = embed_texts([
      term_text(t.get("display_name", ""), t.get("description", ""))
      for t in terms
  ])
  entry_vecs = embed_texts([entry_text(e) for e in entries])

  out: List[Dict] = []
  for t, tvec in zip(terms, term_vecs):
    scored: List[Dict] = []
    for entry, evec in zip(entries, entry_vecs):
      if not tvec or not evec:
        continue
      cos = cosine_similarity(tvec, evec)
      if cos < threshold:
        continue
      scored.append({
          "entry_name": entry.get("entry_name"),
          "display_name": entry.get("display_name"),
          "description": entry.get("description"),
          "system": entry.get("system"),
          "resource_id": entry.get("resource_id"),
          "cosine": round(cos, 4),
      })
    scored.sort(key=lambda x: x["cosine"], reverse=True)
    out.append({
        "term_id": t.get("id"),
        "term_display_name": t.get("display_name", ""),
        "term_description": t.get("description", ""),
        "entries": scored[:top_k_per_term],
    })
  return {"candidates": out}
