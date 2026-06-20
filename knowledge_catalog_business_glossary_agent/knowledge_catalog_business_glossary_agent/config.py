"""Centralized configuration for the Business Glossary Agent.

Semantic-core features (embeddings, LLM relationship classifier, lineage)
are *hard-required* once their env vars are set: tools surface the underlying
error rather than silently degrading. Leave the env var unset to disable the
feature and run the agent in V1 lexical mode.
"""

import os
from functools import lru_cache


# ---------------------------------------------------------------------------
# Core project / model
# ---------------------------------------------------------------------------

def get_consumer_project() -> str:
  """Returns the consumer (billing) project ID from the environment."""
  project = os.environ.get("GOOGLE_CLOUD_PROJECT")
  if not project:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable is required.")
  return project


def get_default_location() -> str:
  """Default Dataplex location for glossary resources."""
  return os.environ.get("DATAPLEX_GLOSSARY_LOCATION", "global")


def get_vertex_location() -> str:
  """Vertex AI region for embeddings + classifier calls."""
  return os.environ.get("VERTEX_LOCATION", "us-central1")


def get_model_name() -> str:
  """Full Gemini model path used by the agent."""
  project = get_consumer_project()
  model = os.environ.get(
      "GLOSSARY_AGENT_MODEL", "gemini-3-flash-preview"
  )
  return (
      f"projects/{project}/locations/global/publishers/google/models/{model}"
  )


def get_classifier_model() -> str:
  """Bare Gemini model id used by the relationship classifier (no path)."""
  return os.environ.get(
      "GLOSSARY_AGENT_CLASSIFIER_MODEL", "gemini-3-flash-preview"
  )


def get_dataplex_endpoint() -> str:
  return os.environ.get("DATAPLEX_API_ENDPOINT", "dataplex.googleapis.com")


def get_dataplex_base_url() -> str:
  return f"https://{get_dataplex_endpoint()}/v1"


# ---------------------------------------------------------------------------
# Ingestion limits
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Semantic core: embeddings + clustering + LLM classifier
# ---------------------------------------------------------------------------

def get_embedding_model() -> str:
  """Vertex text embedding model id (e.g. text-embedding-005)."""
  return os.environ.get(
      "GLOSSARY_AGENT_EMBEDDING_MODEL", "text-embedding-005"
  )


def get_embedding_dim() -> int:
  """Optional output dimensionality override (0 = model default of 768)."""
  return int(os.environ.get("GLOSSARY_AGENT_EMBEDDING_DIM", "0"))


def get_embedding_batch_size() -> int:
  """Texts per Vertex embedding request."""
  return int(os.environ.get("GLOSSARY_AGENT_EMBEDDING_BATCH", "100"))


def get_link_cosine_threshold() -> float:
  """Cosine threshold below which a candidate (term, entry) link is dropped
  before the LLM relationship classifier even runs.
  """
  return float(os.environ.get("GLOSSARY_AGENT_LINK_COSINE_MIN", "0.45"))


def get_link_strong_cosine_threshold() -> float:
  """Cosine at/above which a candidate is treated as a strong match and
  triggers lineage-based propagation (and skips low-confidence verifier
  re-runs).
  """
  return float(os.environ.get("GLOSSARY_AGENT_LINK_COSINE_STRONG", "0.72"))


def get_dedup_cosine_threshold() -> float:
  """When extending an existing glossary, a proposed term whose embedding
  is within this cosine of an existing term's embedding is considered a
  duplicate and dropped (or downgraded to an "alias" suggestion).
  """
  return float(os.environ.get("GLOSSARY_AGENT_DEDUP_COSINE", "0.78"))


def get_cluster_distance_threshold() -> float:
  """Cosine distance threshold for AgglomerativeClustering (auto cluster
  count). Lower = more, smaller clusters.
  """
  return float(os.environ.get("GLOSSARY_AGENT_CLUSTER_DISTANCE", "0.55"))


def get_min_cluster_size() -> int:
  """Minimum members per cluster before it can become a category."""
  return int(os.environ.get("GLOSSARY_AGENT_MIN_CLUSTER_SIZE", "3"))


def get_max_categories() -> int:
  """Hard cap on category proposals (PRD: 3-10)."""
  return int(os.environ.get("GLOSSARY_AGENT_MAX_CATEGORIES", "10"))


def get_max_terms() -> int:
  """Hard cap on term proposals per run (PRD: 10-40)."""
  return int(os.environ.get("GLOSSARY_AGENT_MAX_TERMS", "40"))


def get_max_classifier_pairs() -> int:
  """Hard cap on (term, entry) pairs sent to the LLM relationship
  classifier in one turn — protects cost and latency.
  """
  return int(os.environ.get("GLOSSARY_AGENT_MAX_CLASSIFIER_PAIRS", "200"))


# ---------------------------------------------------------------------------
# Data Lineage
# ---------------------------------------------------------------------------

def is_lineage_enabled() -> bool:
  """Lineage propagation is opt-in via env flag. When True the link agent
  expands strong-definition links to upstream/downstream entries as
  'related'.
  """
  return os.environ.get("GLOSSARY_AGENT_USE_LINEAGE", "").strip().lower() in (
      "1", "true", "yes",
  )


def get_lineage_location() -> str:
  return os.environ.get("LINEAGE_LOCATION", "us")


def get_lineage_max_hops() -> int:
  return int(os.environ.get("LINEAGE_MAX_HOPS", "1"))


def get_lineage_max_neighbors() -> int:
  return int(os.environ.get("LINEAGE_MAX_NEIGHBORS", "25"))


# ---------------------------------------------------------------------------
# Vertex SDK init (idempotent)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def init_vertex() -> None:
  """Initializes the vertexai SDK once per process. Idempotent."""
  import vertexai  # local import keeps cold-start cheap if unused

  vertexai.init(
      project=get_consumer_project(),
      location=get_vertex_location(),
  )
