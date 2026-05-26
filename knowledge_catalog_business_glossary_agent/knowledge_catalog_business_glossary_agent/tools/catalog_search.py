"""Knowledge Catalog Search tools.

Wraps the Dataplex SearchEntries + LookupContext APIs so the glossary agent
can pull catalog entries (tables, datasets, etc.) that should be considered
when recommending an ontology or asset-to-term links. Modeled on the
knowledge_catalog_discovery_agent for behavioral parity.
"""

import concurrent.futures
import logging
from typing import Dict, List, Union

from google.api_core import retry
from google.api_core.exceptions import PermissionDenied
from google.cloud import dataplex_v1

from ..config import get_consumer_project, get_dataplex_endpoint

logger = logging.getLogger(__name__)

MAX_WORKERS = 5
_TRANSIENT_RETRY = retry.Retry(
    predicate=retry.if_transient_error,
    initial=2.0,
    maximum=2.0,
    multiplier=1.0,
    timeout=9.0,
)


def _client() -> dataplex_v1.CatalogServiceClient:
  return dataplex_v1.CatalogServiceClient(
      client_options={"api_endpoint": get_dataplex_endpoint()}
  )


def _search_one(query: str, page_size: int) -> Dict[str, Union[List[Dict], str]]:
  try:
    project = get_consumer_project()
  except ValueError as e:
    return {"error": str(e)}

  try:
    parent = f"projects/{project}/locations/global"
    response = _client().search_entries(
        request={
            "name": parent,
            "query": query,
            "page_size": page_size,
            "semantic_search": True,
        },
        retry=_TRANSIENT_RETRY,
    )
    entries = [
        {
            "entry_name": r.dataplex_entry.name,
            "system": r.dataplex_entry.entry_source.system,
            "resource_id": r.dataplex_entry.entry_source.resource,
            "display_name": r.dataplex_entry.entry_source.display_name,
            "description": r.dataplex_entry.entry_source.description,
        }
        for r in response.results
    ]
    return {"results": entries}
  except PermissionDenied:
    return {"error": "Permission denied calling Knowledge Catalog Search."}
  except Exception as e:  # pylint: disable=broad-except
    logger.exception("Search failed for query '%s'", query)
    return {"error": f"Unexpected error: {e}"}


def _lookup_context(region: str, batch: List[str]) -> str:
  try:
    project = get_consumer_project()
  except ValueError as e:
    return f"Error obtaining consumer project: {e}"
  try:
    parent = f"projects/{project}/locations/{region}"
    request = dataplex_v1.LookupContextRequest(name=parent, resources=batch)
    response = _client().lookup_context(request=request, retry=_TRANSIENT_RETRY)
    return response.context
  except Exception as e:  # pylint: disable=broad-except
    logger.warning("LookupContext failed in %s: %s", region, e)
    return ""


def _merge_round_robin(
    per_query: List[List[Dict]], page_size: int
) -> List[Dict]:
  merged: List[Dict] = []
  seen: set = set()
  depth = 0
  max_depth = max((len(r) for r in per_query), default=0)
  while depth < max_depth and len(merged) < page_size:
    for results in per_query:
      if depth < len(results):
        item = results[depth]
        name = item.get("entry_name")
        if name and name not in seen:
          merged.append(item)
          seen.add(name)
          if len(merged) >= page_size:
            break
    depth += 1
  return merged


def knowledge_catalog_multi_search(
    queries: List[str],
) -> Dict[str, Union[List[Dict], str]]:
  """Runs multiple Knowledge Catalog searches in parallel and merges results.

  Use this when scoping a glossary recommendation to a domain or filter
  expressed in natural language (e.g., "customer billing tables in project X").

  Args:
      queries: Natural-language query variations. Each may include KC predicates
          like ``type=table`` or ``projectid=foo``. Provide up to ~5 variations.

  Returns:
      A dict with ``results`` (deduplicated entries) and ``combined_context``
      (LookupContext text for the merged set), or an ``error``.
  """
  if not queries:
    return {"results": []}

  page_size = 100
  per_query: List[List[Dict]] = []
  errors: List[str] = []

  with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
    futures = [pool.submit(_search_one, q, page_size) for q in queries]
    for q, fut in zip(queries, futures):
      try:
        res = fut.result()
        if "results" in res:
          per_query.append(res["results"])
        else:
          errors.append(f"{q}: {res.get('error')}")
          per_query.append([])
      except Exception as e:  # pylint: disable=broad-except
        errors.append(f"{q}: {e}")
        per_query.append([])

  if errors and len(errors) == len(queries):
    return {"error": "All search queries failed.", "details": errors}

  merged = _merge_round_robin(per_query, page_size)

  # Group merged entries by region for LookupContext batching.
  by_region: Dict[str, List[str]] = {}
  for item in merged:
    name = item["entry_name"]
    parts = name.split("/")
    region = (
        parts[3]
        if len(parts) >= 4 and parts[0] == "projects" and parts[2] == "locations"
        else "global"
    )
    by_region.setdefault(region, []).append(name)

  batches: List[tuple] = []
  for region, names in by_region.items():
    for i in range(0, len(names), 10):
      batches.append((region, names[i : i + 10]))

  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    ctx_futures = [pool.submit(_lookup_context, r, b) for r, b in batches]
    concurrent.futures.wait(ctx_futures)
    contexts = [f.result() for f in ctx_futures]

  return {
      "results": merged,
      "combined_context": "\n\n".join(filter(None, contexts)),
  }
