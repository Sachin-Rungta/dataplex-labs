"""Link recommendation sub-agent.

Pipeline (per term):
  1. ``suggest_link_candidates_semantic`` ranks entries by cosine.
  2. ``classify_relationships`` (batched) picks the EntryLinkType.
  3. ``get_lineage_neighbors`` expands strong-definition links to upstream
     + downstream catalog entries proposed as ``related`` (opt-in via
     ``GLOSSARY_AGENT_USE_LINEAGE``).
  4. ``existing_link_targets_for_terms`` strips duplicates against links
     that already exist.
"""

import os

from google.adk.agents import llm_agent
from google.adk.models import google_llm

from ..config import get_model_name
from ..tools import (
    classify_relationships,
    entry_to_fqn,
    existing_link_targets_for_terms,
    get_lineage_neighbors,
    lineage_status,
    list_entry_links_for_term,
    list_glossary_terms,
    suggest_link_candidates,
    suggest_link_candidates_bulk,
    suggest_link_candidates_semantic,
)

_PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "prompts",
    "link_recommender.md",
)


def _load_instruction() -> str:
  with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
    return f.read()


link_recommendation_agent = llm_agent.Agent(
    model=google_llm.Gemini(model=get_model_name()),
    name="glossary_link_recommendation_agent",
    description=(
        "Proposes EntryLinks between glossary terms and catalog entries"
        " using embedding cosine + LLM relationship classifier, with"
        " optional lineage-based propagation. Returns structured JSON"
        " proposals; never writes."
    ),
    instruction=_load_instruction(),
    tools=[
        # Semantic candidate ranking.
        suggest_link_candidates_semantic,
        suggest_link_candidates_bulk,
        # LLM relationship classifier (batched).
        classify_relationships,
        # Lineage propagation.
        lineage_status,
        get_lineage_neighbors,
        entry_to_fqn,
        # Duplicate-link guard.
        existing_link_targets_for_terms,
        # Lexical fallback + glossary inspection.
        suggest_link_candidates,
        list_glossary_terms,
        list_entry_links_for_term,
    ],
)
