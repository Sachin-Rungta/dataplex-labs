"""Ingestion sub-agent: builds the context graph."""

import os

from google.adk.agents import llm_agent
from google.adk.models import google_llm

from ..config import get_model_name
from ..tools import (
    build_context_graph,
    documentai_status,
    extract_with_documentai,
    knowledge_catalog_multi_search,
    list_gcs_documents,
    read_gcs_document,
    summarize_context_graph,
)

_PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "prompts", "ingestion_agent.md"
)


def _load_instruction() -> str:
  with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
    return f.read()


ingestion_agent = llm_agent.Agent(
    model=google_llm.Gemini(model=get_model_name()),
    name="glossary_ingestion_agent",
    description=(
        "Builds a context graph from Knowledge Catalog entries and / or"
        " unstructured GCS documents to ground glossary recommendations."
    ),
    instruction=_load_instruction(),
    tools=[
        knowledge_catalog_multi_search,
        list_gcs_documents,
        read_gcs_document,
        extract_with_documentai,
        documentai_status,
        build_context_graph,
        summarize_context_graph,
    ],
)
