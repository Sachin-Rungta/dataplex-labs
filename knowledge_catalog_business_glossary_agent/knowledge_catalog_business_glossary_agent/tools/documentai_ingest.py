"""Document AI extraction for PDFs, scanned images, and Office files.

This module is intentionally tolerant of misconfiguration: if the
``DOCUMENT_AI_PROCESSOR_ID`` env var is empty, ``extract_with_documentai``
returns a structured "disabled" result instead of raising. The caller
(``gcs_ingest.read_gcs_document``) treats that as "skip this binary doc"
and the rest of the agent continues with whatever text it does have.

Recommended processor: **Layout Parser**, which handles PDF, DOCX, PPTX,
XLSX, HTML, and TXT and preserves block / heading structure that is
useful for glossary extraction. The plain OCR processor also works and
is the cheapest option when the corpus is PDF-only.
"""

import logging
import mimetypes
from typing import Dict, List, Optional

from google.api_core.client_options import ClientOptions
from google.cloud import documentai, storage

from ..config import (
    get_consumer_project,
    get_documentai_location,
    get_documentai_processor_id,
    get_documentai_processor_version,
    get_max_gcs_doc_bytes,
    is_documentai_enabled,
)
from ..utils import parse_gcs_uri

logger = logging.getLogger(__name__)

# Extensions DocAI can process given the recommended Layout Parser processor.
# PDFs and scanned images are also handled by the OCR processor.
SUPPORTED_EXTENSIONS = (
    ".pdf",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif",
    ".docx", ".pptx", ".xlsx", ".html", ".htm",
)

# Explicit MIME map for extensions where the OS guess is unreliable.
_EXT_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    ".html": "text/html",
    ".htm": "text/html",
}


def is_documentai_supported(uri_or_name: str) -> bool:
  """Returns True if the file extension is handleable by DocAI."""
  lower = uri_or_name.lower()
  return any(lower.endswith(ext) for ext in SUPPORTED_EXTENSIONS)


def _guess_mime(uri: str) -> str:
  lower = uri.lower()
  for ext, mime in _EXT_MIME.items():
    if lower.endswith(ext):
      return mime
  guessed, _ = mimetypes.guess_type(uri)
  return guessed or "application/octet-stream"


def _processor_name() -> str:
  project = get_consumer_project()
  location = get_documentai_location()
  pid = get_documentai_processor_id()
  version = get_documentai_processor_version()
  base = f"projects/{project}/locations/{location}/processors/{pid}"
  return f"{base}/processorVersions/{version}" if version else base


def _client() -> documentai.DocumentProcessorServiceClient:
  endpoint = f"{get_documentai_location()}-documentai.googleapis.com"
  return documentai.DocumentProcessorServiceClient(
      client_options=ClientOptions(api_endpoint=endpoint)
  )


def _download_bytes(gcs_uri: str, cap: int) -> bytes:
  bucket_name, blob_path = parse_gcs_uri(gcs_uri)
  if not blob_path:
    raise ValueError(f"GCS URI must include an object path: {gcs_uri}")
  blob = storage.Client().bucket(bucket_name).blob(blob_path)
  blob.reload()
  end = (cap or blob.size or 0) - 1 if cap else None
  if end is not None and end >= 0:
    return blob.download_as_bytes(start=0, end=end)
  return blob.download_as_bytes()


def _extract_layout_chunks(document) -> List[Dict]:
  """Pulls structured chunks from a DocAI response.

  Layout Parser exposes ``document.chunked_document.chunks``. OCR responses
  don't include chunks; for those we synthesize a single full-text chunk.
  """
  chunked = getattr(document, "chunked_document", None)
  if chunked and getattr(chunked, "chunks", None):
    out = []
    for ch in chunked.chunks:
      out.append({
          "chunk_id": getattr(ch, "chunk_id", "") or "",
          "content": getattr(ch, "content", "") or "",
          "page_span": [
              getattr(span, "page_start", 0)
              for span in getattr(ch, "page_span", []) or []
          ],
      })
    return out
  return [{"chunk_id": "full", "content": document.text or "", "page_span": []}]


def extract_with_documentai(
    gcs_uri: str,
    max_bytes: Optional[int] = None,
) -> Dict:
  """Runs Document AI on a single GCS object and returns extracted text.

  Args:
      gcs_uri: ``gs://bucket/path/file.pdf`` (or other supported type).
      max_bytes: Cap on downloaded bytes (default from config).

  Returns:
      On success: ``{"uri", "text", "chunks": [...], "mime_type", "pages": int}``
      When DocAI is disabled: ``{"uri", "skipped": "documentai_disabled"}``
      On error: ``{"uri", "error": "..."}``
  """
  if not is_documentai_enabled():
    return {"uri": gcs_uri, "skipped": "documentai_disabled"}

  if not is_documentai_supported(gcs_uri):
    return {"uri": gcs_uri, "error": "unsupported file type for DocAI"}

  cap = max_bytes or get_max_gcs_doc_bytes() * 8  # Binary docs are larger than text.
  try:
    raw = _download_bytes(gcs_uri, cap)
  except Exception as e:  # pylint: disable=broad-except
    logger.exception("DocAI: failed to download %s", gcs_uri)
    return {"uri": gcs_uri, "error": f"download failed: {e}"}

  mime = _guess_mime(gcs_uri)
  request = documentai.ProcessRequest(
      name=_processor_name(),
      raw_document=documentai.RawDocument(content=raw, mime_type=mime),
  )

  try:
    response = _client().process_document(request=request)
  except Exception as e:  # pylint: disable=broad-except
    logger.exception("DocAI process_document failed for %s", gcs_uri)
    return {"uri": gcs_uri, "error": f"DocAI call failed: {e}"}

  document = response.document
  return {
      "uri": gcs_uri,
      "mime_type": mime,
      "text": document.text or "",
      "chunks": _extract_layout_chunks(document),
      "pages": len(getattr(document, "pages", []) or []),
  }


def documentai_status() -> Dict:
  """Returns a human-readable status block for the agent / README."""
  if not is_documentai_enabled():
    return {
        "enabled": False,
        "reason": (
            "DOCUMENT_AI_PROCESSOR_ID not set; binary documents (PDF, DOCX,"
            " images) will be skipped during ingestion."
        ),
    }
  return {
      "enabled": True,
      "processor": _processor_name(),
      "supported_extensions": list(SUPPORTED_EXTENSIONS),
  }
