"""Read unstructured documents from a GCS bucket for ontology grounding.

Text-native types (md, txt, csv, json, ...) are read directly. Binary types
(PDF, scanned images, DOCX, PPTX, XLSX, HTML) are routed through Document AI
when ``DOCUMENT_AI_PROCESSOR_ID`` is configured. When DocAI is disabled,
binary docs are listed but skipped during reading (the caller sees a
``skipped`` marker rather than an error so ingestion can continue).
"""

import logging
from typing import Dict, List, Optional

from google.cloud import storage

from ..config import (
    get_max_gcs_doc_bytes,
    get_max_gcs_docs,
    is_documentai_enabled,
)
from ..utils import parse_gcs_uri
from .documentai_ingest import (
    SUPPORTED_EXTENSIONS as _DOCAI_EXTS,
    extract_with_documentai,
    is_documentai_supported,
)

logger = logging.getLogger(__name__)

_TEXT_EXTS = (
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".xml", ".log", ".sql",
)


def _default_extensions() -> tuple:
  """Text extensions plus DocAI-supported extensions if DocAI is on."""
  if is_documentai_enabled():
    return tuple(sorted(set(_TEXT_EXTS) | set(_DOCAI_EXTS)))
  return _TEXT_EXTS


def list_gcs_documents(
    gcs_uri: str,
    extensions: Optional[List[str]] = None,
    max_docs: Optional[int] = None,
) -> Dict:
  """Lists candidate documents under a ``gs://bucket/prefix`` location.

  When DocAI is enabled the default extension set includes PDF, scanned
  images, DOCX, PPTX, XLSX, and HTML in addition to the plain-text types.

  Args:
      gcs_uri: GCS URI to a bucket or prefix (e.g., ``gs://my-bucket/glossary/``).
      extensions: Restrict to these lowercase extensions (overrides default).
      max_docs: Cap on number of documents returned (default from config).

  Returns:
      ``{"documents": [{"uri", "size_bytes", "content_type", "needs_documentai"}],
         "truncated": bool, "documentai_enabled": bool}`` or ``{"error": ...}``.
  """
  try:
    bucket_name, prefix = parse_gcs_uri(gcs_uri)
  except ValueError as e:
    return {"error": str(e)}

  exts = tuple(e.lower() for e in (extensions or _default_extensions()))
  limit = max_docs or get_max_gcs_docs()
  client = storage.Client()
  docs: List[Dict] = []
  try:
    for blob in client.list_blobs(bucket_name, prefix=prefix):
      if blob.name.endswith("/"):
        continue
      lower = blob.name.lower()
      if not lower.endswith(exts):
        continue
      docs.append({
          "uri": f"gs://{bucket_name}/{blob.name}",
          "size_bytes": blob.size or 0,
          "content_type": blob.content_type or "",
          "needs_documentai": is_documentai_supported(lower),
      })
      if len(docs) >= limit:
        break
  except Exception as e:  # pylint: disable=broad-except
    logger.exception("Failed to list %s", gcs_uri)
    return {"error": f"List failed: {e}"}

  return {
      "documents": docs,
      "truncated": len(docs) >= limit,
      "documentai_enabled": is_documentai_enabled(),
  }


def read_gcs_document(gcs_uri: str, max_bytes: Optional[int] = None) -> Dict:
  """Reads a single GCS object, routing binary types through Document AI.

  Returns one of:
    * ``{"uri", "content", "truncated", "size_bytes", "source": "text"}``
    * ``{"uri", "content", "chunks", "pages", "source": "documentai"}``
    * ``{"uri", "skipped": "documentai_disabled"}``  (binary doc, DocAI off)
    * ``{"uri", "error": "..."}``
  """
  try:
    bucket_name, blob_path = parse_gcs_uri(gcs_uri)
  except ValueError as e:
    return {"uri": gcs_uri, "error": str(e)}

  if not blob_path:
    return {"uri": gcs_uri, "error": "GCS URI must include an object path."}

  lower = blob_path.lower()

  # --- Binary path: Document AI ---
  if is_documentai_supported(lower) and not lower.endswith((".html", ".htm")):
    # HTML is listed by DocAI as supported (Layout Parser) but is also
    # readable as text; keep the cheaper text path unless explicitly needed.
    extracted = extract_with_documentai(gcs_uri, max_bytes=max_bytes)
    if "error" in extracted or "skipped" in extracted:
      return extracted
    return {
        "uri": gcs_uri,
        "content": extracted.get("text", ""),
        "chunks": extracted.get("chunks", []),
        "pages": extracted.get("pages", 0),
        "source": "documentai",
    }

  # --- Text path ---
  cap = max_bytes or get_max_gcs_doc_bytes()
  try:
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_path)
    raw = blob.download_as_bytes(start=0, end=cap - 1)
    text = raw.decode("utf-8", errors="replace")
    return {
        "uri": gcs_uri,
        "content": text,
        "truncated": (blob.size or 0) > cap,
        "size_bytes": blob.size or 0,
        "source": "text",
    }
  except Exception as e:  # pylint: disable=broad-except
    logger.exception("Failed to read %s", gcs_uri)
    return {"uri": gcs_uri, "error": f"Read failed: {e}"}
