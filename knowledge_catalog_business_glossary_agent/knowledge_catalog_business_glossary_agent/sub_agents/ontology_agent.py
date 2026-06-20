"""Ontology recommendation sub-agent.

Two paths:

* **new-glossary mode** — propose a brand-new glossary identity,
  3-10 categories and 10-40 terms with cited evidence.
* **extend-existing-glossary mode** — fetch the existing glossary's
  categories + terms, dedupe candidate terms against them by cosine
  similarity, and propose only *net-new* categories + terms (or aliases
  to existing terms when appropriate).
"""

import os

from google.adk.agents import llm_agent
from google.adk.models import google_llm

from ..config import get_model_name
from ..tools import (
    cluster_concepts_for_categories,
    find_similar_existing_terms,
    find_similar_existing_terms_bulk,
    get_existing_glossary_state,
    list_glossaries,
    list_glossary_categories,
    list_glossary_terms,
    score_term_candidates,
    score_term_candidates_semantic,
)

_PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "prompts",
    "ontology_recommender.md",
)


def _load_instruction() -> str:
  with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
    return f.read()


ontology_recommendation_agent = llm_agent.Agent(
    model=google_llm.Gemini(model=get_model_name()),
    name="glossary_ontology_recommendation_agent",
    description=(
        "Recommends a glossary, categories, and terms from a context graph."
        " Supports both new-glossary mode and extend-existing-glossary mode."
        " Returns structured JSON; never writes."
    ),
    instruction=_load_instruction(),
    tools=[
        # Semantic scorers (V2 spine).
        score_term_candidates_semantic,
        cluster_concepts_for_categories,
        # Lexical fallback (cheap explainable signal).
        score_term_candidates,
        # Existing-glossary inspection (extend mode).
        get_existing_glossary_state,
        find_similar_existing_terms,
        find_similar_existing_terms_bulk,
        # Generic glossary listing (cross-glossary discovery).
        list_glossaries,
        list_glossary_categories,
        list_glossary_terms,
    ],
)
