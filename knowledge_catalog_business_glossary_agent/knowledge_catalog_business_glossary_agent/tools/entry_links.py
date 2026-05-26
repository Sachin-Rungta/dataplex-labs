"""EntryLink CRUD between glossary terms and catalog entries.

EntryLinks attach semantic meaning (a glossary term) to a catalog entry
(table, column, dataset, etc.). The agent uses these to propose and create
the term <-> asset relationships a steward would otherwise click together
manually.

Supported relationship reference types:
  * ``synonym``  -- the asset uses the same concept under a different name
  * ``related``  -- the asset is meaningfully related to the term
  * ``describes`` -- the term describes the asset

The relationship is encoded via the ``entryLinkType`` system reference link
in Dataplex (``projects/dataplex-types/locations/global/entryLinkTypes/...``).
"""

import logging
from typing import Dict, List, Optional

import requests

from ..config import (
    get_consumer_project,
    get_dataplex_base_url,
    get_default_location,
)
from ..utils import get_access_token, parse_entry_name, slugify

logger = logging.getLogger(__name__)
_TIMEOUT = 30

_LINK_TYPES = {
    "synonym": "projects/dataplex-types/locations/global/entryLinkTypes/synonym",
    "related": "projects/dataplex-types/locations/global/entryLinkTypes/related",
    "describes": (
        "projects/dataplex-types/locations/global/entryLinkTypes/definition"
    ),
}


def _headers() -> Dict[str, str]:
  return {
      "Authorization": f"Bearer {get_access_token()}",
      "Content-Type": "application/json",
      "X-Goog-User-Project": get_consumer_project(),
  }


def _request(method: str, url: str, **kwargs) -> Dict:
  try:
    resp = requests.request(
        method, url, headers=_headers(), timeout=_TIMEOUT, **kwargs
    )
    if resp.status_code >= 400:
      logger.warning("%s %s -> %s: %s", method, url, resp.status_code, resp.text)
      return {"error": f"HTTP {resp.status_code}", "details": resp.text}
    return resp.json() if resp.text else {}
  except requests.RequestException as e:
    logger.exception("Request failed: %s %s", method, url)
    return {"error": str(e)}


def _term_entry_name(
    glossary_id: str, term_id: str, location: Optional[str] = None
) -> str:
  """Glossary terms are also exposed as catalog entries; return that name."""
  project = get_consumer_project()
  loc = location or get_default_location()
  return (
      f"projects/{project}/locations/{loc}/entryGroups/@dataplex/"
      f"entries/projects/{project}/locations/{loc}/glossaries/"
      f"{glossary_id}/terms/{term_id}"
  )


def create_entry_link(
    glossary_id: str,
    term_id: str,
    target_entry_name: str,
    relationship: str = "describes",
    entry_link_id: Optional[str] = None,
    location: Optional[str] = None,
) -> Dict:
  """Creates an EntryLink between a glossary term and a catalog entry.

  Args:
      glossary_id: The glossary containing the term.
      term_id: The term resource ID.
      target_entry_name: Fully-qualified entry name (the asset side of the
          relationship), e.g.
          ``projects/p/locations/us/entryGroups/g/entries/bigquery:p.dataset.table``.
      relationship: One of ``synonym``, ``related``, ``describes``.
      entry_link_id: Optional ID; auto-generated if omitted.
      location: Dataplex location for the link (defaults to target's location).
  """
  if relationship not in _LINK_TYPES:
    return {
        "error": (
            f"Unknown relationship '{relationship}'. Expected one of"
            f" {list(_LINK_TYPES)}."
        )
    }

  parsed = parse_entry_name(target_entry_name)
  if not parsed:
    return {"error": f"Invalid target entry name: {target_entry_name}"}

  loc = location or parsed["location"]
  entry_group = parsed["entry_group"]
  project = get_consumer_project()
  parent = (
      f"projects/{project}/locations/{loc}/entryGroups/{entry_group}"
  )

  link_id = entry_link_id or slugify(f"{term_id}-{parsed['entry_id']}")
  term_entry = _term_entry_name(glossary_id, term_id, location)

  body = {
      "entryLinkType": _LINK_TYPES[relationship],
      "entryReferences": [
          {"name": term_entry, "type": "SOURCE"},
          {"name": target_entry_name, "type": "TARGET"},
      ],
  }
  url = (
      f"{get_dataplex_base_url()}/{parent}/entryLinks?entryLinkId={link_id}"
  )
  return _request("POST", url, json=body)


def delete_entry_link(entry_link_name: str) -> Dict:
  """Deletes an EntryLink by its full resource name."""
  url = f"{get_dataplex_base_url()}/{entry_link_name}"
  return _request("DELETE", url)


def list_entry_links_for_term(
    glossary_id: str,
    term_id: str,
    location: Optional[str] = None,
) -> Dict:
  """Lists EntryLinks that reference a glossary term via lookupEntryLinks."""
  loc = location or get_default_location()
  project = get_consumer_project()
  term_entry = _term_entry_name(glossary_id, term_id, location)
  url = (
      f"{get_dataplex_base_url()}/projects/{project}/locations/{loc}"
      f":lookupEntryLinks?entry={term_entry}&pageSize=500"
  )
  return _request("GET", url)


def list_supported_relationships() -> List[str]:
  """Returns the relationship strings accepted by create_entry_link."""
  return list(_LINK_TYPES.keys())
