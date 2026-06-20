"""LLM relationship classifier for (glossary term, catalog entry) pairs.

This is the V2 quality lever from DESIGN.md §4.P3.C: an embedding cosine
gets us *candidates*, but it can't reliably distinguish
``definition`` vs ``synonym`` vs ``related`` vs ``schema-join``. A single
batched Gemini call with structured output (pydantic schema) does that
cleanly.

The classifier is *only* a verifier. It never invents entries — every
input pair must come from the candidate set the link agent assembled.
The verdict can downgrade a pair to ``none``, which the link agent
treats as "drop".
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

from ..config import (
    get_classifier_model,
    get_consumer_project,
    get_max_classifier_pairs,
    get_vertex_location,
)
from .schemas import RelationshipVerdict, RelationshipVerdictList

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are an expert business glossary editor. For each (term, catalog entry)
pair given below, decide which Dataplex EntryLinkType best describes the
relationship.

Use these canonical EntryLinkType values — and NOTHING else:

* "definition"  — the term *defines* / formally describes the entry. This
                  is the most common term ↔ asset link. Use it when the
                  entry primarily *is* the concept the term names
                  (e.g. term "Customer" ↔ table "customer_profile").
* "synonym"     — the term and the entry's display name mean the same
                  thing. Stronger than "definition"; only use when the
                  entry is the canonical record of the term and its name
                  is the term in another form (e.g. term "Customer" ↔
                  table "customers").
* "related"     — the entry uses or references the concept but is not
                  primarily about it (e.g. term "Customer" ↔ table
                  "orders").
* "schema-join" — column-to-column join relationship between two entries
                  (e.g. ``orders.customer_id`` joins ``customers.id``).
                  Only use when BOTH sides are clearly columns and the
                  join key is obvious from the names.
* "none"        — the candidate does not warrant a link. Use this when
                  the cosine signal was misleading (e.g. shared common
                  word like "name" or "id"), the entry is unrelated, or
                  the term is too abstract to attach to this asset.

Return STRICT JSON matching the schema. Output one verdict per input pair,
in the same order. Each verdict must include:
* term_id              — the term id from the input
* target_entry_name    — the entry resource name from the input
* relationship         — one of the values above
* confidence           — float in [0, 1]
* justification        — one short sentence

Rules:
* NEVER invent term_id or target_entry_name values; copy them from input.
* Prefer "none" over a low-confidence wrong label. Stewards trust silence
  more than they trust noise.
"""


def _client():
  """Returns a google-genai client configured for Vertex AI."""
  from google import genai

  return genai.Client(
      vertexai=True,
      project=get_consumer_project(),
      location=get_vertex_location(),
  )


def _format_pair(idx: int, pair: Dict) -> str:
  return (
      f"--- PAIR {idx + 1} ---\n"
      f"term_id: {pair.get('term_id', '')}\n"
      f"term_display_name: {pair.get('term_display_name', '')}\n"
      f"term_description: {pair.get('term_description', '')}\n"
      f"target_entry_name: {pair.get('target_entry_name', '')}\n"
      f"entry_display_name: {pair.get('entry_display_name', '')}\n"
      f"entry_description: {pair.get('entry_description', '')}\n"
      f"cosine: {pair.get('cosine', 0.0):.3f}\n"
  )


def classify_relationships(pairs: Sequence[Dict]) -> Dict:
  """Classifies a batch of (term, entry) candidate pairs.

  Each input pair must include:
    * ``term_id``               (str, required)
    * ``term_display_name``     (str)
    * ``term_description``      (str)
    * ``target_entry_name``     (str, required)
    * ``entry_display_name``    (str)
    * ``entry_description``     (str)
    * ``cosine``                (float)

  Returns:
      ``{
        "verdicts": [
            {
              "term_id": ...,
              "target_entry_name": ...,
              "relationship": ...,
              "confidence": ...,
              "justification": ...,
            },
            ...
        ],
        "truncated_at": <int | None>,
        "verdict_count": int,
      }``

  Pairs are truncated to ``GLOSSARY_AGENT_MAX_CLASSIFIER_PAIRS``.
  """
  pairs = list(pairs)
  if not pairs:
    return {"verdicts": [], "verdict_count": 0, "truncated_at": None}

  cap = get_max_classifier_pairs()
  truncated_at: Optional[int] = None
  if len(pairs) > cap:
    truncated_at = cap
    pairs = pairs[:cap]

  from google.genai import types  # local import keeps cold-start cheap

  body = "\n".join(_format_pair(i, p) for i, p in enumerate(pairs))
  contents = (
      _SYSTEM_PROMPT
      + "\n\nClassify the following pairs:\n\n"
      + body
  )

  client = _client()
  config = types.GenerateContentConfig(
      response_mime_type="application/json",
      response_schema=RelationshipVerdictList,
      temperature=0.1,
  )
  try:
    response = client.models.generate_content(
        model=get_classifier_model(),
        contents=contents,
        config=config,
    )
  except Exception as e:  # pylint: disable=broad-except
    logger.exception("Gemini relationship classifier call failed.")
    return {
        "error": f"Classifier call failed: {e}",
        "verdicts": [],
        "verdict_count": 0,
        "truncated_at": truncated_at,
    }

  parsed = getattr(response, "parsed", None)
  if parsed is None:
    # Fall back to text parsing — should be rare with response_schema set.
    try:
      parsed = RelationshipVerdictList.model_validate_json(response.text or "")
    except Exception:
      logger.exception("Classifier returned non-parseable output.")
      return {
          "error": "Classifier returned malformed output.",
          "raw": (response.text or "")[:2000],
          "verdicts": [],
          "verdict_count": 0,
          "truncated_at": truncated_at,
      }

  # Enforce target-set: every verdict's term_id + target_entry_name must
  # match an input pair (we don't want the classifier silently swapping
  # names). Verdicts that don't match are dropped with a warning.
  allowed = {
      (p["term_id"], p["target_entry_name"])
      for p in pairs
  }
  kept: List[RelationshipVerdict] = []
  dropped: List[Dict] = []
  for v in parsed.verdicts:
    if (v.term_id, v.target_entry_name) not in allowed:
      dropped.append({
          "term_id": v.term_id,
          "target_entry_name": v.target_entry_name,
          "reason": "classifier output does not match an input pair",
      })
      continue
    kept.append(v)

  return {
      "verdicts": [v.model_dump() for v in kept],
      "verdict_count": len(kept),
      "dropped": dropped,
      "truncated_at": truncated_at,
  }
