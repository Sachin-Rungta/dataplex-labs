"""Centralized configuration for the Business Glossary Agent."""

import os


def get_consumer_project() -> str:
  """Returns the consumer (billing) project ID from the environment."""
  project = os.environ.get("GOOGLE_CLOUD_PROJECT")
  if not project:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable is required.")
  return project


def get_default_location() -> str:
  """Default Dataplex location for glossary resources."""
  return os.environ.get("DATAPLEX_GLOSSARY_LOCATION", "global")


def get_model_name() -> str:
  """Full Gemini model path used by the agent."""
  project = get_consumer_project()
  model = os.environ.get(
      "GLOSSARY_AGENT_MODEL", "gemini-3-flash-preview"
  )
  return (
      f"projects/{project}/locations/global/publishers/google/models/{model}"
  )


def get_dataplex_endpoint() -> str:
  return os.environ.get("DATAPLEX_API_ENDPOINT", "dataplex.googleapis.com")


def get_dataplex_base_url() -> str:
  return f"https://{get_dataplex_endpoint()}/v1"


def get_max_gcs_docs() -> int:
  """Cap on number of GCS documents read during ingestion."""
  return int(os.environ.get("GLOSSARY_AGENT_MAX_GCS_DOCS", "50"))


def get_max_gcs_doc_bytes() -> int:
  """Cap on bytes read per GCS document."""
  return int(os.environ.get("GLOSSARY_AGENT_MAX_DOC_BYTES", str(512 * 1024)))


# ---------------------------------------------------------------------------
# Document AI
#
# DocAI handles PDFs, scanned images, and (with a Layout Parser processor)
# DOCX / PPTX / XLSX. A processor must be pre-created in the GCP project.
# If DOCUMENT_AI_PROCESSOR_ID is unset, the agent gracefully degrades and
# silently skips binary documents during GCS ingestion.
# ---------------------------------------------------------------------------

def get_documentai_location() -> str:
  """Region where the DocAI processor lives (e.g. 'us', 'eu')."""
  return os.environ.get("DOCUMENT_AI_LOCATION", "us")


def get_documentai_processor_id() -> str:
  """Processor ID (the trailing UUID, not the full resource name).

  Empty string means DocAI is disabled.
  """
  return os.environ.get("DOCUMENT_AI_PROCESSOR_ID", "").strip()


def get_documentai_processor_version() -> str:
  """Optional processor version. Empty string uses the default version."""
  return os.environ.get("DOCUMENT_AI_PROCESSOR_VERSION", "").strip()


def is_documentai_enabled() -> bool:
  return bool(get_documentai_processor_id())
