"""LLM-as-Judge utilities for the eval harness.

Three judges:

* ``judge_term_matches`` — given a list of (recommended_term, golden_term)
  candidate pairs (already filtered by embedding cosine), the LLM decides
  whether each pair refers to the same business concept.
* ``judge_category_coherence`` — 1-5 rubric on whether the terms grouped
  under a category belong together.
* ``judge_definition_usefulness`` — 1-5 rubric on whether each term's
  definition is accurate and non-tautological.

All three use ``google.genai`` with pydantic ``response_schema`` for
strict structured output. Batched where possible to amortize call cost.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from knowledge_catalog_business_glossary_agent.config import (
    get_classifier_model,
    get_consumer_project,
    get_vertex_location,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas (judge outputs)
# ---------------------------------------------------------------------------

class TermMatchVerdict(BaseModel):
  model_config = ConfigDict(extra="forbid")

  pair_index: int
  matches: bool
  confidence: float = Field(ge=0.0, le=1.0)
  matched_alias: Optional[str] = None
  reasoning: str


class TermMatchVerdictList(BaseModel):
  model_config = ConfigDict(extra="forbid")

  verdicts: List[TermMatchVerdict]


class CategoryCoherenceVerdict(BaseModel):
  model_config = ConfigDict(extra="forbid")

  category_index: int
  score: int = Field(ge=1, le=5)
  reasoning: str


class CategoryCoherenceVerdictList(BaseModel):
  model_config = ConfigDict(extra="forbid")

  verdicts: List[CategoryCoherenceVerdict]


class DefinitionVerdict(BaseModel):
  model_config = ConfigDict(extra="forbid")

  term_index: int
  score: int = Field(ge=1, le=5)
  reasoning: str


class DefinitionVerdictList(BaseModel):
  model_config = ConfigDict(extra="forbid")

  verdicts: List[DefinitionVerdict]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _client():
  from google import genai

  return genai.Client(
      vertexai=True,
      project=get_consumer_project(),
      location=get_vertex_location(),
  )


def _call(prompt: str, schema):
  from google.genai import types

  client = _client()
  config = types.GenerateContentConfig(
      response_mime_type="application/json",
      response_schema=schema,
      temperature=0.0,
  )
  response = client.models.generate_content(
      model=get_classifier_model(),
      contents=prompt,
      config=config,
  )
  parsed = getattr(response, "parsed", None)
  if parsed is None:
    parsed = schema.model_validate_json(response.text or "")
  return parsed


# ---------------------------------------------------------------------------
# Term-match judge
# ---------------------------------------------------------------------------

_TERM_MATCH_SYSTEM = """\
You are evaluating whether recommended business glossary terms refer to
the same concept as expected (golden) terms.

For each candidate pair below, decide ``matches: true`` if the
recommended term and the golden term are the same business concept
(possibly under different names). Use the golden term's ``aliases``
list — a match against an alias counts. Use the descriptions to
disambiguate when names are similar but the meanings differ.

Be strict: a recommended term that has the same NAME but a different
MEANING is not a match. Two terms that are *related* (e.g. "Revenue"
vs "Net Revenue") are NOT matches.

Return one verdict per pair, preserving ``pair_index``. Confidence in
[0, 1]. One-sentence ``reasoning``. ``matched_alias`` is the specific
alias that matched, or null when the display names matched directly.
"""


def judge_term_matches(pairs: List[Dict]) -> List[Dict]:
  """LLM verdict on each (recommended_term, golden_term) candidate pair.

  Each pair must include:
    * ``pair_index`` (int) — preserved in the verdict for alignment.
    * ``recommended``: {display_name, description}
    * ``golden``: {display_name, description, aliases}

  Returns the verdicts as a list of dicts.
  """
  if not pairs:
    return []

  body_chunks: list[str] = []
  for p in pairs:
    body_chunks.append(
        f"--- PAIR {p['pair_index']} ---\n"
        f"recommended.display_name: {p['recommended']['display_name']}\n"
        f"recommended.description:  {p['recommended'].get('description', '')}\n"
        f"golden.display_name:      {p['golden']['display_name']}\n"
        f"golden.description:       {p['golden'].get('description', '')}\n"
        f"golden.aliases:           {json.dumps(p['golden'].get('aliases', []))}\n"
    )

  prompt = _TERM_MATCH_SYSTEM + "\n\nPairs:\n\n" + "\n".join(body_chunks)
  try:
    parsed = _call(prompt, TermMatchVerdictList)
  except Exception as e:
    logger.exception("term-match judge call failed")
    return [
        {
            "pair_index": p["pair_index"],
            "matches": False,
            "confidence": 0.0,
            "matched_alias": None,
            "reasoning": f"judge_error: {e}",
        }
        for p in pairs
    ]
  return [v.model_dump() for v in parsed.verdicts]


# ---------------------------------------------------------------------------
# Category-coherence judge
# ---------------------------------------------------------------------------

_CATEGORY_COHERENCE_SYSTEM = """\
You are scoring the coherence of proposed glossary categories. For each
category, look at the member terms and rate on this rubric:

5 — All terms belong together; the category name describes them well.
4 — Most terms fit; one or two are weak but still defensible.
3 — Mixed; the category is salvageable but at least one term doesn't fit.
2 — Several terms don't belong; the grouping is forced.
1 — Incoherent; terms are unrelated or the name is misleading.

Return one verdict per category preserving ``category_index``. Provide
a one-sentence ``reasoning``.
"""


def judge_category_coherence(categories: List[Dict]) -> List[Dict]:
  """LLM rubric on each category's coherence.

  Each input category dict must include:
    * ``category_index`` (int)
    * ``display_name`` (str)
    * ``description`` (str)
    * ``member_terms`` (List[str]) — display names of terms in this category.
  """
  if not categories:
    return []
  body_chunks = []
  for c in categories:
    body_chunks.append(
        f"--- CATEGORY {c['category_index']} ---\n"
        f"display_name: {c['display_name']}\n"
        f"description:  {c['description']}\n"
        f"member_terms: {json.dumps(c['member_terms'])}\n"
    )
  prompt = _CATEGORY_COHERENCE_SYSTEM + "\n\nCategories:\n\n" + "\n".join(body_chunks)
  try:
    parsed = _call(prompt, CategoryCoherenceVerdictList)
  except Exception as e:
    logger.exception("category-coherence judge call failed")
    return [
        {
            "category_index": c["category_index"],
            "score": 0,
            "reasoning": f"judge_error: {e}",
        }
        for c in categories
    ]
  return [v.model_dump() for v in parsed.verdicts]


# ---------------------------------------------------------------------------
# Definition-usefulness judge
# ---------------------------------------------------------------------------

_DEFINITION_SYSTEM = """\
You are scoring the usefulness of business glossary term definitions.
For each term, rate on this rubric:

5 — Plain-English, accurate, specific, non-tautological. A non-engineer
    could read it once and understand what the term means in this domain.
4 — Accurate; minor specificity issues or one slightly technical phrase.
3 — Mostly accurate but vague (e.g. "represents the customer") or partly
    tautological ("Customer is the customer in the system").
2 — Wrong or actively misleading on at least one substantive point.
1 — Empty, gibberish, or contradicts the term name.

Return one verdict per term preserving ``term_index``, with a
one-sentence reasoning.
"""


def judge_definition_usefulness(
    terms: List[Dict],
    domain_context: str,
) -> List[Dict]:
  """LLM rubric on each term's definition usefulness.

  Each term dict must include:
    * ``term_index`` (int)
    * ``display_name`` (str)
    * ``description`` (str)
  """
  if not terms:
    return []
  body_chunks = []
  for t in terms:
    body_chunks.append(
        f"--- TERM {t['term_index']} ---\n"
        f"display_name: {t['display_name']}\n"
        f"description:  {t['description']}\n"
    )
  prompt = (
      _DEFINITION_SYSTEM
      + f"\n\nDomain context: {domain_context}\n\nTerms:\n\n"
      + "\n".join(body_chunks)
  )
  try:
    parsed = _call(prompt, DefinitionVerdictList)
  except Exception as e:
    logger.exception("definition-usefulness judge call failed")
    return [
        {
            "term_index": t["term_index"],
            "score": 0,
            "reasoning": f"judge_error: {e}",
        }
        for t in terms
    ]
  return [v.model_dump() for v in parsed.verdicts]
