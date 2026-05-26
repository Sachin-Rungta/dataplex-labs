"""Knowledge Catalog Business Glossary Agent (root)."""

import os

from google.adk.agents import llm_agent
from google.adk.models import google_llm
from google.adk.tools.agent_tool import AgentTool

from .config import get_model_name
from .sub_agents import (
    ingestion_agent,
    link_recommendation_agent,
    ontology_recommendation_agent,
)
from .tools import (
    create_entry_link,
    create_glossary,
    create_glossary_category,
    create_glossary_term,
    delete_entry_link,
    delete_glossary,
    delete_glossary_category,
    delete_glossary_term,
    get_glossary,
    list_entry_links_for_term,
    list_glossaries,
    list_glossary_categories,
    list_glossary_terms,
    update_glossary_term,
)
from .utils import configure_logging

configure_logging()

_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "prompts", "root_instructions.md"
)


def _load_instruction() -> str:
  with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
    return f.read()


root_agent = llm_agent.Agent(
    model=google_llm.Gemini(model=get_model_name()),
    name="knowledge_catalog_business_glossary_agent",
    description=(
        "Recommends a Dataplex business glossary (categories, terms) and"
        " asset-to-term EntryLinks a data steward can create. Supports full"
        " lifecycle (create / update / delete) with explicit approval, and"
        " grounds recommendations in Knowledge Catalog entries and"
        " unstructured GCS documents."
    ),
    instruction=_load_instruction(),
    tools=[
        # Sub-agents exposed as tools the root agent can call.
        AgentTool(agent=ingestion_agent),
        AgentTool(agent=ontology_recommendation_agent),
        AgentTool(agent=link_recommendation_agent),
        # Glossary CRUD — used only after explicit approval.
        list_glossaries,
        get_glossary,
        create_glossary,
        delete_glossary,
        list_glossary_categories,
        create_glossary_category,
        delete_glossary_category,
        list_glossary_terms,
        create_glossary_term,
        update_glossary_term,
        delete_glossary_term,
        # EntryLink CRUD.
        list_entry_links_for_term,
        create_entry_link,
        delete_entry_link,
    ],
)
