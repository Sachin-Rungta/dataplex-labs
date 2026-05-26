"""Lightweight context graph used to ground ontology recommendations.

The graph is intentionally simple and deterministic so the LLM can reason
about it: nodes are candidate concepts derived from catalog entries and from
unstructured GCS documents, edges record co-occurrence between concepts.

The graph is not persisted; it's built per turn and handed back to the LLM
as compact structured context. The LLM is responsible for promoting nodes
to glossary terms and choosing relationship types.
"""

import logging
import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional

from .catalog_search import knowledge_catalog_multi_search
from .gcs_ingest import list_gcs_documents, read_gcs_document

logger = logging.getLogger(__name__)

# Tokens shorter than this are skipped (noise).
_MIN_TOKEN_LEN = 4
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")

# Lowercase stopwords kept short on purpose — the LLM filters further.
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "have", "into",
    "your", "their", "about", "which", "when", "what", "where", "will",
    "they", "them", "there", "these", "those", "would", "could", "should",
    "such", "than", "then", "also", "data", "table", "tables", "column",
    "columns", "value", "values", "field", "fields", "type", "types",
    "name", "names", "true", "false", "null", "none", "string", "integer",
    "boolean", "float", "double", "object", "array", "record", "rows",
    "json", "yaml", "https", "http", "json", "html",
})


def tokenize(text: str) -> List[str]:
  return [
      t.lower()
      for t in _TOKEN_RE.findall(text or "")
      if len(t) >= _MIN_TOKEN_LEN and t.lower() not in _STOPWORDS
  ]


def _entry_concepts(entry: Dict) -> List[str]:
  text = " ".join(
      str(entry.get(k, ""))
      for k in ("display_name", "resource_id", "description")
  )
  return tokenize(text)


def build_context_graph(
    queries: Optional[List[str]] = None,
    gcs_uri: Optional[str] = None,
    max_docs: int = 20,
    top_n_concepts: int = 60,
) -> Dict:
  """Builds a co-occurrence graph from catalog entries and/or GCS documents.

  Either ``queries`` or ``gcs_uri`` (or both) must be provided. Catalog
  entries pulled from ``queries`` contribute concept frequencies; documents
  under ``gcs_uri`` add additional concept evidence and link concepts that
  appear in the same document.

  Args:
      queries: Optional KC search query variations to fetch entries.
      gcs_uri: Optional ``gs://bucket/prefix`` for unstructured docs.
      max_docs: Cap on documents to read from GCS.
      top_n_concepts: Cap on concepts retained in the returned graph.

  Returns:
      ``{
          "concepts": [{"name": str, "frequency": int, "sources": [str]}],
          "edges":    [{"source": str, "target": str, "weight": int}],
          "entries":  [...],  # raw catalog entries
          "documents": [...], # per-doc concept list (no raw text)
      }``
  """
  if not queries and not gcs_uri:
    return {"error": "Provide at least one of 'queries' or 'gcs_uri'."}

  concept_freq: Counter = Counter()
  concept_sources: Dict[str, set] = defaultdict(set)
  edges: Counter = Counter()
  entries: List[Dict] = []
  docs_summary: List[Dict] = []

  # ---- catalog side ----
  if queries:
    search = knowledge_catalog_multi_search(queries)
    if "error" in search:
      logger.warning("catalog search failed: %s", search["error"])
    else:
      entries = search.get("results", [])
      for entry in entries:
        tokens = list(dict.fromkeys(_entry_concepts(entry)))
        for t in tokens:
          concept_freq[t] += 1
          concept_sources[t].add(entry.get("entry_name", ""))
        for i, a in enumerate(tokens):
          for b in tokens[i + 1 : i + 8]:
            if a != b:
              edges[tuple(sorted((a, b)))] += 1

  # ---- doc side ----
  if gcs_uri:
    listing = list_gcs_documents(gcs_uri, max_docs=max_docs)
    if "error" in listing:
      logger.warning("gcs listing failed: %s", listing["error"])
    else:
      for doc in listing.get("documents", []):
        content = read_gcs_document(doc["uri"])
        if "error" in content:
          docs_summary.append(
              {"uri": doc["uri"], "status": "error", "detail": content["error"]}
          )
          continue
        if "skipped" in content:
          docs_summary.append(
              {"uri": doc["uri"], "status": "skipped",
               "detail": content["skipped"]}
          )
          continue
        tokens = list(dict.fromkeys(tokenize(content.get("content", ""))))
        for t in tokens:
          concept_freq[t] += 1
          concept_sources[t].add(doc["uri"])
        for i, a in enumerate(tokens):
          for b in tokens[i + 1 : i + 8]:
            if a != b:
              edges[tuple(sorted((a, b)))] += 1
        docs_summary.append({
            "uri": doc["uri"],
            "status": "ok",
            "source": content.get("source", "text"),
            "concept_count": len(tokens),
            "pages": content.get("pages", 0),
        })

  top_concepts = [c for c, _ in concept_freq.most_common(top_n_concepts)]
  top_set = set(top_concepts)

  return {
      "concepts": [
          {
              "name": c,
              "frequency": concept_freq[c],
              "sources": sorted(concept_sources[c])[:5],
          }
          for c in top_concepts
      ],
      "edges": [
          {"source": a, "target": b, "weight": w}
          for (a, b), w in edges.most_common(top_n_concepts * 3)
          if a in top_set and b in top_set
      ],
      "entries": entries,
      "documents": docs_summary,
  }


def summarize_context_graph(graph: Dict, max_lines: int = 80) -> str:
  """Renders the graph as compact text suitable for prompt grounding."""
  if "error" in graph:
    return f"(no context graph: {graph['error']})"
  lines: List[str] = ["CONCEPTS (concept | freq | sources):"]
  for c in graph.get("concepts", [])[:max_lines]:
    lines.append(
        f"  - {c['name']} | {c['frequency']} | {', '.join(c['sources'][:2])}"
    )
  lines.append("EDGES (a -- b | weight):")
  for e in graph.get("edges", [])[:max_lines]:
    lines.append(f"  - {e['source']} -- {e['target']} | {e['weight']}")
  return "\n".join(lines)


def shared_concepts(a: Iterable[str], b: Iterable[str]) -> List[str]:
  return sorted(set(a) & set(b))
