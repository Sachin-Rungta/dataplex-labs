"""Headless ontology + link recommendation for the eval harness.

Bypasses the ADK runner (which is conversational + non-deterministic)
and calls Gemini directly with the same prompts, tools, and structured
output the live agent uses. This gives the eval:

* deterministic invocation order (build_context_graph → embed →
  cluster → ontology prompt → link pipeline);
* a strict pydantic-shaped response (no markdown to parse);
* much faster wall-clock per eval run (no chat-turn ping-pong).

It does NOT exercise the ADK root-agent orchestration or the steward
approval gates. Those need a separate ADK-based eval (future round).
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from knowledge_catalog_business_glossary_agent.config import (
    get_classifier_model,
    get_consumer_project,
    get_link_cosine_threshold,
    get_max_terms,
    get_vertex_location,
)
from knowledge_catalog_business_glossary_agent.tools import (
    build_context_graph,
    classify_relationships,
    cluster_concepts_for_categories,
    embed_context_graph,
    get_existing_glossary_state,
    score_term_candidates_semantic,
    suggest_link_candidates_bulk,
    summarize_context_graph,
)
from knowledge_catalog_business_glossary_agent.tools.schemas import (
    LinkRecommendation,
    OntologyRecommendation,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt (inline; mirrors prompts/ontology_recommender.md but assumes the
# scored candidates + clusters + existing-state are already injected)
# ---------------------------------------------------------------------------

_ONTOLOGY_SYSTEM = """\
You are the Business Glossary Ontology Recommender (eval harness).
You are given a context graph, ranked term candidates, cluster
proposals, and (in extend modes) the existing glossary state. Produce
an OntologyRecommendation JSON object.

Hard rules:
* Every term's ``evidence`` MUST be an entry resource name from
  ``graph.entries`` or a gs:// URI from ``graph.documents`` — never
  invent either, never reference "existing glossary entries".
* Term ``id`` must be kebab-case and unique within the response.
* In ``mode = "new"``: include a ``glossary`` block; categories all
  have ``existing: false``.
* In ``mode = "extend"``: omit ``glossary``; set ``glossary_id`` and
  ``glossary_location``; mark reused categories ``existing: true``.
* In ``mode = "extend-terms-only"``: same as ``extend`` but NEVER
  propose a new category. Every term must attach to one of the
  existing categories. Candidate terms that don't fit any existing
  category go into ``unmatched_terms``.
* Cap terms at the supplied ``max_terms``; set ``truncated_at_terms``
  if you truncated.
* Use the candidate ``confidence`` (≈ semantic score) to pick which
  terms make the cut.
* Definitions must be one sentence, in plain business English a
  non-engineer can read.
"""


def _build_ontology_payload(
    *,
    graph_summary: str,
    candidates: Dict,
    clusters: Dict,
    mode: str,
    scope_hint: str,
    max_terms: int,
    glossary_id: Optional[str],
    glossary_location: Optional[str],
    existing_state: Optional[Dict],
    must_include: Optional[List[str]],
    must_exclude: Optional[List[str]],
) -> str:
  pieces = [
      _ONTOLOGY_SYSTEM,
      f"\n## mode\n{mode}",
      f"\n## scope_hint\n{scope_hint}",
      f"\n## max_terms\n{max_terms}",
      f"\n## context_graph_summary\n{graph_summary}",
      f"\n## ranked_candidates\n{json.dumps(candidates, indent=2)}",
      f"\n## cluster_proposals\n{json.dumps(clusters, indent=2)}",
  ]
  if glossary_id:
    pieces.append(f"\n## glossary_id\n{glossary_id}")
  if glossary_location:
    pieces.append(f"\n## glossary_location\n{glossary_location}")
  if existing_state:
    pieces.append(
        f"\n## existing_glossary_state\n{json.dumps(existing_state, indent=2)}"
    )
  if must_include:
    pieces.append(f"\n## must_include_terms\n{json.dumps(must_include)}")
  if must_exclude:
    pieces.append(f"\n## must_exclude_terms\n{json.dumps(must_exclude)}")
  pieces.append(
      "\n## output\n"
      "Return strict JSON for OntologyRecommendation. No prose, no"
      " markdown fences."
  )
  return "\n".join(pieces)


def _client():
  from google import genai

  return genai.Client(
      vertexai=True,
      project=get_consumer_project(),
      location=get_vertex_location(),
  )


def recommend_ontology_headless(
    *,
    catalog_queries: List[str],
    gcs_uri: Optional[str],
    scope_hint: str,
    mode: str = "new",
    glossary_id: Optional[str] = None,
    glossary_location: Optional[str] = None,
    must_include_terms: Optional[List[str]] = None,
    must_exclude_terms: Optional[List[str]] = None,
    max_terms: Optional[int] = None,
) -> Dict:
  """Runs the full ontology pipeline headlessly.

  Returns a dict shaped like:
    {
      "graph_stats": {entries, documents, concepts, edges},
      "candidates": [...],
      "clusters": [...],
      "recommendation": OntologyRecommendation (dict),
      "prompt": "...",   # included for debugging / prompt iteration
    }
  """
  # 1. Build context graph
  graph = build_context_graph(queries=catalog_queries, gcs_uri=gcs_uri)
  if "error" in graph:
    return {"error": graph["error"]}

  # 2. Embed concepts + entries
  embed_stats = embed_context_graph(graph)
  if "error" in embed_stats:
    return {"error": embed_stats["error"]}

  # 3. Score candidates + cluster
  candidates = score_term_candidates_semantic(graph, scope_hint=scope_hint)
  clusters = cluster_concepts_for_categories(graph)

  # 4. (Extend modes) load existing state
  existing_state: Optional[Dict] = None
  if mode in ("extend", "extend-terms-only"):
    if not glossary_id:
      return {"error": f"mode={mode} requires glossary_id"}
    state = get_existing_glossary_state(glossary_id, glossary_location)
    if "error" in state:
      return {"error": f"existing-glossary load failed: {state['error']}"}
    existing_state = state

  cap = max_terms or get_max_terms()

  # 5. Build prompt + call Gemini with structured output
  from google.genai import types

  payload = _build_ontology_payload(
      graph_summary=summarize_context_graph(graph),
      candidates=candidates,
      clusters=clusters,
      mode=mode,
      scope_hint=scope_hint,
      max_terms=cap,
      glossary_id=glossary_id,
      glossary_location=glossary_location,
      existing_state=existing_state,
      must_include=must_include_terms,
      must_exclude=must_exclude_terms,
  )
  client = _client()
  config = types.GenerateContentConfig(
      response_mime_type="application/json",
      response_schema=OntologyRecommendation,
      temperature=0.1,
  )
  try:
    response = client.models.generate_content(
        model=get_classifier_model(),
        contents=payload,
        config=config,
    )
  except Exception as e:  # pylint: disable=broad-except
    logger.exception("Ontology recommendation call failed.")
    return {"error": f"recommendation call failed: {e}"}

  parsed = getattr(response, "parsed", None)
  if parsed is None:
    try:
      parsed = OntologyRecommendation.model_validate_json(response.text or "")
    except Exception as e:
      return {
          "error": f"recommender returned unparseable output: {e}",
          "raw": (response.text or "")[:4000],
      }

  return {
      "graph_stats": {
          "entries": len(graph.get("entries", []) or []),
          "documents": len(graph.get("documents", []) or []),
          "concepts": len(graph.get("concepts", []) or []),
          "edges": len(graph.get("edges", []) or []),
      },
      "embed_stats": embed_stats,
      "candidates": candidates.get("candidates", []),
      "clusters": clusters,
      "recommendation": parsed.model_dump(),
      "graph_entries": graph.get("entries", []),
      "graph_documents": graph.get("documents", []),
  }


def recommend_links_headless(
    *,
    terms: List[Dict],          # each: {id, display_name, description}
    entries: List[Dict],        # context-graph entries
    min_cosine: Optional[float] = None,
) -> Dict:
  """Runs the link recommendation pipeline (cosine ranking + classifier)."""
  if not terms or not entries:
    return {"recommendation": LinkRecommendation(proposals=[]).model_dump()}

  threshold = (
      min_cosine if min_cosine is not None else get_link_cosine_threshold()
  )

  # 1. Candidate ranking via cosine
  bulk = suggest_link_candidates_bulk(terms, entries, min_cosine=threshold)
  pairs: List[Dict] = []
  for per_term in bulk.get("candidates", []):
    for ent in per_term.get("entries", []):
      pairs.append({
          "term_id": per_term["term_id"],
          "term_display_name": per_term["term_display_name"],
          "term_description": per_term["term_description"],
          "target_entry_name": ent["entry_name"],
          "entry_display_name": ent["display_name"],
          "entry_description": ent.get("description", ""),
          "cosine": ent["cosine"],
      })

  if not pairs:
    return {"recommendation": LinkRecommendation(proposals=[]).model_dump()}

  # 2. LLM relationship classifier (batched)
  verdict_resp = classify_relationships(pairs)
  if verdict_resp.get("error"):
    return {"error": verdict_resp["error"]}

  pair_by_key = {(p["term_id"], p["target_entry_name"]): p for p in pairs}

  proposals: List[Dict] = []
  skipped: List[Dict] = []
  for v in verdict_resp.get("verdicts", []):
    key = (v["term_id"], v["target_entry_name"])
    pair = pair_by_key.get(key)
    if v["relationship"] == "none":
      skipped.append({
          "term_id": v["term_id"],
          "target_entry_name": v["target_entry_name"],
          "reason": "classifier_none",
      })
      continue
    proposals.append({
        "term_id": v["term_id"],
        "term_display_name": pair["term_display_name"] if pair else "",
        "target_entry_name": v["target_entry_name"],
        "relationship": v["relationship"],
        "score": round(
            (v["confidence"] or 0.0) * (pair["cosine"] if pair else 1.0), 4
        ),
        "cosine": pair["cosine"] if pair else None,
        "rationale": v["justification"],
        "classifier_confidence": v["confidence"],
    })

  proposals.sort(
      key=lambda p: (p["classifier_confidence"] or 0.0, p["cosine"] or 0.0),
      reverse=True,
  )

  rec = LinkRecommendation(proposals=proposals, skipped=skipped)
  return {"recommendation": rec.model_dump(), "n_pairs": len(pairs)}
