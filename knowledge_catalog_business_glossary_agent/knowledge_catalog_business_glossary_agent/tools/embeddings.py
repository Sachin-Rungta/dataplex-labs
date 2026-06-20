"""Vertex AI text embeddings with per-process content-hash caching.

A single process-level cache keyed by sha256(text) means each unique string
is embedded at most once, even when several tools (clustering, semantic
scoring, dedup, link recommendation) all consult embeddings for the same
concepts within a turn.

The cache is intentionally non-persistent: every fresh agent process is a
clean slate. That keeps the agent reproducible and avoids carrying stale
embedding-model versions across upgrades.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from typing import Iterable, List, Optional, Sequence

from ..config import (
    get_embedding_batch_size,
    get_embedding_dim,
    get_embedding_model,
    init_vertex,
)

logger = logging.getLogger(__name__)

# Module-level cache. Tools call ``embed_texts`` and trust the cache to
# dedupe. Vectors are stored as plain lists for cheap JSON round-tripping
# even though we never actually serialize them out of process.
_CACHE: dict[str, List[float]] = {}


def _key(text: str) -> str:
  return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_size() -> int:
  """Number of distinct texts currently embedded in-process."""
  return len(_CACHE)


def clear_cache() -> None:
  """Drops the in-process embedding cache (used by tests)."""
  _CACHE.clear()


# ---------------------------------------------------------------------------
# Vertex client
# ---------------------------------------------------------------------------

def _embed_batch(texts: Sequence[str]) -> List[List[float]]:
  """Single Vertex AI call. Returns one vector per input text."""
  # Imports kept local: the agent boots even if google-cloud-aiplatform is
  # missing, as long as embeddings aren't called.
  from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

  init_vertex()
  model = TextEmbeddingModel.from_pretrained(get_embedding_model())
  # task_type = RETRIEVAL_DOCUMENT is a good default for unsymmetric
  # similarity (term ↔ entry description). text-embedding-005 ignores
  # task_type for some configurations but accepting it harms nothing.
  inputs = [TextEmbeddingInput(text=t, task_type="RETRIEVAL_DOCUMENT") for t in texts]
  kwargs = {}
  dim = get_embedding_dim()
  if dim:
    kwargs["output_dimensionality"] = dim
  embeddings = model.get_embeddings(inputs, **kwargs)
  return [list(e.values) for e in embeddings]


def embed_texts(texts: Iterable[str]) -> List[List[float]]:
  """Returns a vector per input text, using cache for repeats.

  Empty / whitespace-only inputs are mapped to ``None`` placeholders so
  callers don't accidentally embed garbage. The returned list preserves
  input order.
  """
  texts = list(texts)
  if not texts:
    return []

  # Identify cache misses while preserving order.
  needed: list[tuple[int, str]] = []
  for i, t in enumerate(texts):
    if not t or not t.strip():
      continue
    if _key(t) not in _CACHE:
      needed.append((i, t))

  if needed:
    batch_size = max(1, get_embedding_batch_size())
    for start in range(0, len(needed), batch_size):
      chunk = needed[start : start + batch_size]
      try:
        vectors = _embed_batch([t for _, t in chunk])
      except Exception:
        logger.exception(
            "Vertex embedding call failed (batch of %d).", len(chunk)
        )
        raise
      for (_, text), vec in zip(chunk, vectors):
        _CACHE[_key(text)] = vec

  result: List[List[float]] = []
  for t in texts:
    if not t or not t.strip():
      result.append([])
      continue
    result.append(_CACHE[_key(t)])
  return result


def embed_one(text: str) -> List[float]:
  """Convenience helper for a single string."""
  return embed_texts([text])[0]


# ---------------------------------------------------------------------------
# Similarity math
# ---------------------------------------------------------------------------

def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
  """Plain Python cosine. We deliberately do not import numpy here because
  link ranking calls cosine on small fixed lists where the pure-Python
  loop is faster than a numpy roundtrip.
  """
  if not a or not b:
    return 0.0
  dot = 0.0
  na = 0.0
  nb = 0.0
  for x, y in zip(a, b):
    dot += x * y
    na += x * x
    nb += y * y
  if na == 0.0 or nb == 0.0:
    return 0.0
  return dot / (math.sqrt(na) * math.sqrt(nb))


def pairwise_cosine(
    queries: Sequence[Sequence[float]],
    targets: Sequence[Sequence[float]],
) -> List[List[float]]:
  """Returns a ``len(queries) x len(targets)`` cosine matrix."""
  return [[cosine_similarity(q, t) for t in targets] for q in queries]


def top_k_targets(
    query: Sequence[float],
    targets: Sequence[Sequence[float]],
    *,
    k: int,
    min_score: float = 0.0,
) -> List[tuple[int, float]]:
  """Returns the (index, cosine) of the K nearest targets to ``query``."""
  scored: list[tuple[int, float]] = []
  for i, t in enumerate(targets):
    s = cosine_similarity(query, t)
    if s >= min_score:
      scored.append((i, s))
  scored.sort(key=lambda x: x[1], reverse=True)
  return scored[:k]


# ---------------------------------------------------------------------------
# Concept / entry text builders
#
# Centralized so every tool that embeds a concept or entry uses the same
# string. That's important: two different builders for "the same thing"
# would silently double cache misses and yield two distinct vectors with
# different cosines.
# ---------------------------------------------------------------------------

def concept_text(name: str, neighbors: Optional[Iterable[str]] = None) -> str:
  """Builds the embedding string for a context-graph concept.

  Including the strongest co-occurrence neighbors gives a single-word
  concept like "customer" enough surrounding context to embed
  meaningfully — without them, "customer" is too ambiguous to score
  against a verbose table description.
  """
  base = name.strip()
  if neighbors:
    extras = ", ".join(sorted({n for n in neighbors if n and n != name})[:5])
    if extras:
      return f"business concept: {base} (related: {extras})"
  return f"business concept: {base}"


def entry_text(entry: dict) -> str:
  """Embedding string for a Knowledge Catalog entry."""
  parts = [
      str(entry.get("display_name", "") or ""),
      str(entry.get("resource_id", "") or ""),
      str(entry.get("description", "") or ""),
  ]
  return "\n".join(p for p in parts if p).strip() or "(unnamed entry)"


def term_text(display_name: str, description: str = "") -> str:
  """Embedding string for a glossary term (existing or proposed)."""
  if description:
    return f"{display_name}: {description}"
  return display_name
