"""Data Lineage helper for link propagation.

When the link recommender finds a *strong* definition link between a term
T and a catalog entry E, the entries upstream and downstream of E in the
data lineage graph are usually *related* to T. This module wraps the
Data Lineage v1 API so the link agent can:

* take a list of seed catalog entries that have just been linked to a
  term, and
* expand them to a set of neighbor entries that should be proposed as
  ``related`` links.

The Lineage API works on *Fully Qualified Names* (FQNs) like
``bigquery:project.dataset.table``, not on Dataplex entry resource
names. We translate between the two using the Dataplex entry's
``resource_id`` field (which already carries the FQN-style identifier
for first-class systems like BigQuery).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

from ..config import (
    get_consumer_project,
    get_lineage_location,
    get_lineage_max_hops,
    get_lineage_max_neighbors,
    is_lineage_enabled,
)

logger = logging.getLogger(__name__)


def _system_prefix(system: str) -> Optional[str]:
  """Maps a Knowledge Catalog ``system`` to a Data Lineage FQN prefix."""
  if not system:
    return None
  s = system.lower()
  if "bigquery" in s:
    return "bigquery"
  if "cloudsql" in s:
    return "cloudsql"
  if "spanner" in s:
    return "spanner"
  if "cloud-storage" in s or "storage" in s:
    return "cloud-storage"
  # Custom systems are commonly already FQN-shaped (``mysystem:host.db.t``).
  return s


def entry_to_fqn(entry: Dict) -> Optional[str]:
  """Builds a Data Lineage FQN for a Knowledge Catalog entry, or None.

  Order of preference:
    1. If ``entry['fqn']`` already exists, return it.
    2. If ``entry['resource_id']`` contains a colon (``bigquery:p.ds.t``),
       it's already FQN-shaped.
    3. Otherwise synthesize ``{system}:{resource_id}``.
  """
  fqn = entry.get("fqn") or entry.get("fully_qualified_name")
  if fqn:
    return fqn
  rid = entry.get("resource_id") or ""
  if ":" in rid:
    return rid
  prefix = _system_prefix(entry.get("system", ""))
  if prefix and rid:
    return f"{prefix}:{rid}"
  return None


def lineage_status() -> Dict:
  """Returns a status block the agent can include in steward messages."""
  if not is_lineage_enabled():
    return {
        "enabled": False,
        "reason": (
            "GLOSSARY_AGENT_USE_LINEAGE is not set; lineage propagation"
            " is skipped."
        ),
    }
  return {
      "enabled": True,
      "location": get_lineage_location(),
      "max_hops": get_lineage_max_hops(),
      "max_neighbors": get_lineage_max_neighbors(),
  }


def _client():
  from google.cloud import datacatalog_lineage_v1

  return datacatalog_lineage_v1.LineageClient()


def get_lineage_neighbors(
    seed_fqns: Sequence[str],
    *,
    max_hops: Optional[int] = None,
    max_neighbors: Optional[int] = None,
) -> Dict:
  """Returns upstream + downstream FQNs for each seed FQN.

  Args:
      seed_fqns: list of Data Lineage FQNs (e.g. ``bigquery:p.ds.t``).
      max_hops: BFS hops out from each seed. Defaults to env.
      max_neighbors: hard cap on neighbors *per direction per seed*.

  Returns:
      ``{
        "enabled": bool,
        "neighbors": {
            "<seed_fqn>": {
                "upstream":   ["fqn1", "fqn2", ...],
                "downstream": ["fqn3", ...],
            },
            ...
        },
        "errors": [{"seed": "...", "error": "..."}],
      }``
  """
  if not is_lineage_enabled():
    return {
        "enabled": False,
        "reason": (
            "Lineage is disabled (GLOSSARY_AGENT_USE_LINEAGE not set)."
        ),
    }

  hops = max_hops if max_hops is not None else get_lineage_max_hops()
  cap = max_neighbors if max_neighbors is not None else get_lineage_max_neighbors()

  if not seed_fqns:
    return {"enabled": True, "neighbors": {}, "errors": []}

  from google.cloud import datacatalog_lineage_v1

  client = _client()
  project = get_consumer_project()
  location = get_lineage_location()
  parent = f"projects/{project}/locations/{location}"

  out_neighbors: Dict[str, Dict[str, List[str]]] = {}
  errors: List[Dict[str, str]] = []

  for seed in seed_fqns:
    upstream: list[str] = []
    downstream: list[str] = []
    frontier_up: set[str] = {seed}
    frontier_down: set[str] = {seed}
    seen_up: set[str] = set()
    seen_down: set[str] = set()

    try:
      for _ in range(max(1, hops)):
        # upstream: source = ?  target = frontier_up nodes
        next_up: set[str] = set()
        for fqn in list(frontier_up):
          target = datacatalog_lineage_v1.EntityReference(
              fully_qualified_name=fqn
          )
          req = datacatalog_lineage_v1.SearchLinksRequest(
              parent=parent, target=target
          )
          for link in client.search_links(request=req):
            src = link.source.fully_qualified_name
            if src and src != fqn and src not in seen_up:
              seen_up.add(src)
              next_up.add(src)
              upstream.append(src)
              if len(upstream) >= cap:
                break
          if len(upstream) >= cap:
            break
        frontier_up = next_up
        if not frontier_up or len(upstream) >= cap:
          break

      for _ in range(max(1, hops)):
        next_down: set[str] = set()
        for fqn in list(frontier_down):
          source = datacatalog_lineage_v1.EntityReference(
              fully_qualified_name=fqn
          )
          req = datacatalog_lineage_v1.SearchLinksRequest(
              parent=parent, source=source
          )
          for link in client.search_links(request=req):
            tgt = link.target.fully_qualified_name
            if tgt and tgt != fqn and tgt not in seen_down:
              seen_down.add(tgt)
              next_down.add(tgt)
              downstream.append(tgt)
              if len(downstream) >= cap:
                break
          if len(downstream) >= cap:
            break
        frontier_down = next_down
        if not frontier_down or len(downstream) >= cap:
          break

      out_neighbors[seed] = {
          "upstream": upstream[:cap],
          "downstream": downstream[:cap],
      }
    except Exception as e:  # pylint: disable=broad-except
      logger.exception("Lineage lookup failed for %s", seed)
      errors.append({"seed": seed, "error": str(e)})

  return {
      "enabled": True,
      "neighbors": out_neighbors,
      "errors": errors,
  }
