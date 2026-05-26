"""Link recommendation sub-agent."""

import os

from google.adk.agents import llm_agent
from google.adk.models import google_llm

from ..config import get_model_name
from ..tools import (
    list_entry_links_for_term,
    list_glossary_terms,
    suggest_link_candidates,
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
        "Proposes EntryLinks between glossary terms and catalog entries."
        " Returns structured JSON proposals; never writes."
    ),
    instruction=_load_instruction(),
    tools=[
        suggest_link_candidates,
        list_glossary_terms,
        list_entry_links_for_term,
    ],
)
