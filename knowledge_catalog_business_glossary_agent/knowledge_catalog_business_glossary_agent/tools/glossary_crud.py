"""Full CRUD over Dataplex Business Glossary resources.

Uses the Dataplex REST API directly because some glossary surface area is not
yet wrapped by the generated google-cloud-dataplex Python client at parity
with REST. Auth is via Application Default Credentials.

Resource hierarchy:
  projects/{project}/locations/{location}/glossaries/{glossary}
    .../categories/{category}
    .../terms/{term}
"""

import logging
from typing import Dict, List, Optional

import requests

from ..config import (
    get_consumer_project,
    get_dataplex_base_url,
    get_default_location,
)
from ..utils import get_access_token, slugify

logger = logging.getLogger(__name__)
_TIMEOUT = 30


def _headers() -> Dict[str, str]:
  project = get_consumer_project()
  return {
      "Authorization": f"Bearer {get_access_token()}",
      "Content-Type": "application/json",
      "X-Goog-User-Project": project,
  }


def _glossary_parent(location: Optional[str] = None) -> str:
  loc = location or get_default_location()
  return f"projects/{get_consumer_project()}/locations/{loc}"


def _request(method: str, url: str, **kwargs) -> Dict:
  """Issues an authenticated request and returns parsed JSON or an error dict."""
  try:
    resp = requests.request(
        method, url, headers=_headers(), timeout=_TIMEOUT, **kwargs
    )
    if resp.status_code >= 400:
      logger.warning("%s %s -> %s: %s", method, url, resp.status_code, resp.text)
      return {
          "error": f"HTTP {resp.status_code}",
          "details": resp.text,
      }
    return resp.json() if resp.text else {}
  except requests.RequestException as e:
    logger.exception("Request failed: %s %s", method, url)
    return {"error": str(e)}


# ---------------------------------------------------------------------------
# Glossaries
# ---------------------------------------------------------------------------

def list_glossaries(location: Optional[str] = None) -> Dict:
  """Lists glossaries in the given location (default: 'global')."""
  parent = _glossary_parent(location)
  url = f"{get_dataplex_base_url()}/{parent}/glossaries"
  return _request("GET", url)


def get_glossary(glossary_id: str, location: Optional[str] = None) -> Dict:
  """Fetches a glossary by ID."""
  parent = _glossary_parent(location)
  url = f"{get_dataplex_base_url()}/{parent}/glossaries/{glossary_id}"
  return _request("GET", url)


def create_glossary(
    glossary_id: str,
    display_name: str,
    description: str = "",
    location: Optional[str] = None,
) -> Dict:
  """Creates a new business glossary.

  Args:
      glossary_id: The resource ID for the glossary (lowercase, hyphens).
      display_name: Human-readable name shown in the UI.
      description: Optional description.
      location: Dataplex location; defaults to ``DATAPLEX_GLOSSARY_LOCATION``.
  """
  parent = _glossary_parent(location)
  url = (
      f"{get_dataplex_base_url()}/{parent}/glossaries"
      f"?glossaryId={slugify(glossary_id)}"
  )
  body = {"displayName": display_name, "description": description}
  return _request("POST", url, json=body)


def delete_glossary(glossary_id: str, location: Optional[str] = None) -> Dict:
  """Deletes a glossary (must be empty)."""
  parent = _glossary_parent(location)
  url = f"{get_dataplex_base_url()}/{parent}/glossaries/{glossary_id}"
  return _request("DELETE", url)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

def list_glossary_categories(
    glossary_id: str, location: Optional[str] = None
) -> Dict:
  """Lists categories under a glossary."""
  parent = f"{_glossary_parent(location)}/glossaries/{glossary_id}"
  url = f"{get_dataplex_base_url()}/{parent}/categories"
  return _request("GET", url)


def create_glossary_category(
    glossary_id: str,
    category_id: str,
    display_name: str,
    description: str = "",
    parent_category_id: Optional[str] = None,
    location: Optional[str] = None,
) -> Dict:
  """Creates a category in a glossary, optionally nested under a parent."""
  loc = location or get_default_location()
  project = get_consumer_project()
  glossary_parent = (
      f"projects/{project}/locations/{loc}/glossaries/{glossary_id}"
  )
  url = (
      f"{get_dataplex_base_url()}/{glossary_parent}/categories"
      f"?categoryId={slugify(category_id)}"
  )
  body = {"displayName": display_name, "description": description}
  if parent_category_id:
    body["parent"] = f"{glossary_parent}/categories/{parent_category_id}"
  return _request("POST", url, json=body)


def delete_glossary_category(
    glossary_id: str, category_id: str, location: Optional[str] = None
) -> Dict:
  """Deletes a category."""
  parent = f"{_glossary_parent(location)}/glossaries/{glossary_id}"
  url = f"{get_dataplex_base_url()}/{parent}/categories/{category_id}"
  return _request("DELETE", url)


# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------

def list_glossary_terms(
    glossary_id: str, location: Optional[str] = None
) -> Dict:
  """Lists terms in a glossary."""
  parent = f"{_glossary_parent(location)}/glossaries/{glossary_id}"
  url = f"{get_dataplex_base_url()}/{parent}/terms?pageSize=1000"
  return _request("GET", url)


def create_glossary_term(
    glossary_id: str,
    term_id: str,
    display_name: str,
    description: str = "",
    category_id: Optional[str] = None,
    location: Optional[str] = None,
) -> Dict:
  """Creates a term in a glossary, optionally nested under a category."""
  loc = location or get_default_location()
  project = get_consumer_project()
  glossary_parent = (
      f"projects/{project}/locations/{loc}/glossaries/{glossary_id}"
  )
  url = (
      f"{get_dataplex_base_url()}/{glossary_parent}/terms"
      f"?termId={slugify(term_id)}"
  )
  body = {"displayName": display_name, "description": description}
  if category_id:
    body["parent"] = f"{glossary_parent}/categories/{category_id}"
  return _request("POST", url, json=body)


def update_glossary_term(
    glossary_id: str,
    term_id: str,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
) -> Dict:
  """Patches a term's display name and/or description."""
  parent = f"{_glossary_parent(location)}/glossaries/{glossary_id}"
  body: Dict = {}
  mask: List[str] = []
  if display_name is not None:
    body["displayName"] = display_name
    mask.append("displayName")
  if description is not None:
    body["description"] = description
    mask.append("description")
  if not mask:
    return {"error": "No fields supplied to update."}
  url = (
      f"{get_dataplex_base_url()}/{parent}/terms/{term_id}"
      f"?updateMask={','.join(mask)}"
  )
  return _request("PATCH", url, json=body)


def delete_glossary_term(
    glossary_id: str, term_id: str, location: Optional[str] = None
) -> Dict:
  """Deletes a term."""
  parent = f"{_glossary_parent(location)}/glossaries/{glossary_id}"
  url = f"{get_dataplex_base_url()}/{parent}/terms/{term_id}"
  return _request("DELETE", url)
