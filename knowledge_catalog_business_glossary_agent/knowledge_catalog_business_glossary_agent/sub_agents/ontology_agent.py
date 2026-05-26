"""Ontology recommendation sub-agent."""

import os

from google.adk.agents import llm_agent
from google.adk.models import google_llm

from ..config import get_model_name
from ..tools import (
    list_glossaries,
    list_glossary_categories,
    list_glossary_terms,
    score_term_candidates,
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
        " Returns structured JSON; never writes."
    ),
    instruction=_load_instruction(),
    tools=[
        score_term_candidates,
        list_glossaries,
        list_glossary_categories,
        list_glossary_terms,
    ],
)
