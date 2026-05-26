"""Shared helpers for the Business Glossary Agent."""

import logging
import re
from typing import Optional, Tuple

import google.auth
import google.auth.transport.requests

_GCS_URI_RE = re.compile(r"^gs://(?P<bucket>[^/]+)(?:/(?P<prefix>.*))?$")
_ENTRY_NAME_RE = re.compile(
    r"^projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)/"
    r"entryGroups/(?P<entry_group>[^/]+)/entries/(?P<entry_id>.+)$"
)


def parse_gcs_uri(uri: str) -> Tuple[str, str]:
  """Parses a gs:// URI into (bucket, prefix). Prefix is '' if missing."""
  match = _GCS_URI_RE.match(uri.strip())
  if not match:
    raise ValueError(
        f"Invalid GCS URI '{uri}'. Expected format: gs://bucket[/prefix]."
    )
  return match.group("bucket"), match.group("prefix") or ""


def parse_entry_name(entry_name: str) -> Optional[dict]:
  """Splits a fully-qualified entry resource name into parts, or None."""
  match = _ENTRY_NAME_RE.match(entry_name)
  if not match:
    return None
  return match.groupdict()


def slugify(value: str, max_len: int = 63) -> str:
  """Lowercase, hyphen-separated, safe-for-resource-id slug."""
  slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
  return slug[:max_len] or "term"


def get_access_token() -> str:
  """Returns an OAuth access token from Application Default Credentials."""
  creds, _ = google.auth.default(
      scopes=["https://www.googleapis.com/auth/cloud-platform"]
  )
  creds.refresh(google.auth.transport.requests.Request())
  return creds.token


def configure_logging() -> None:
  """Idempotent logger setup matching the rest of dataplex-labs agents."""
  if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
