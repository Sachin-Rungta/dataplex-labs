"""Cluster context-graph concepts into category seeds.

Agglomerative clustering with cosine distance + auto cluster count is a
good fit for glossary categories because:

* it tolerates very different cluster sizes (some categories have 2 terms,
  some have 15) without a ``k`` hyperparameter, and
* the cosine distance threshold maps intuitively to "how similar must two
  concepts be to share a category" — a steward-tunable knob.

We deliberately do NOT name categories here. The LLM names them in the
ontology recommender prompt using the cluster's exemplar concepts.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Sequence

from ..config import (
    get_cluster_distance_threshold,
    get_max_categories,
    get_min_cluster_size,
)
from .embeddings import concept_text, embed_texts

logger = logging.getLogger(__name__)


def cluster_concepts(
    concepts: Sequence[Dict],
    *,
    distance_threshold: float | None = None,
    min_cluster_size: int | None = None,
    max_clusters: int | None = None,
) -> Dict:
  """Groups concepts by embedding similarity.

  Args:
      concepts: list of dicts shaped ``{"name": str, "neighbors": [str]}``
          (any extra fields are passed through unchanged).
      distance_threshold: cosine distance ceiling; smaller → tighter
          clusters. Defaults to ``GLOSSARY_AGENT_CLUSTER_DISTANCE``.
      min_cluster_size: clusters smaller than this collapse into the
          "miscellaneous" bucket and are not promoted to categories.
      max_clusters: ranks clusters by total concept frequency and keeps
          the top N; the rest land in "miscellaneous".

  Returns:
      ``{
        "clusters": [
            {
              "cluster_id": str,
              "size": int,
              "exemplars": [str],   # representative concept names
              "concepts": [str],    # all concepts in this cluster
            },
            ...
        ],
        "miscellaneous": [str],   # ungrouped / under-size concepts
      }``
  """
  if not concepts:
    return {"clusters": [], "miscellaneous": []}

  dt = (
      distance_threshold
      if distance_threshold is not None
      else get_cluster_distance_threshold()
  )
  mc = (
      min_cluster_size
      if min_cluster_size is not None
      else get_min_cluster_size()
  )
  cap = max_clusters if max_clusters is not None else get_max_categories()

  texts = [
      concept_text(c.get("name", ""), c.get("neighbors", []))
      for c in concepts
  ]
  vectors = embed_texts(texts)

  # Drop any concepts whose embedding came back empty (shouldn't happen
  # for non-empty inputs but defensive coding here is cheap).
  pairs = [
      (i, v) for i, v in enumerate(vectors) if v and len(v) > 0
  ]
  if len(pairs) < 2:
    # Trivial cases — no clustering possible.
    bucket = [concepts[i].get("name", "") for i, _ in pairs]
    return {
        "clusters": [],
        "miscellaneous": bucket,
    }

  # Imports kept local so the agent can boot without sklearn when this
  # tool isn't called.
  import numpy as np
  from sklearn.cluster import AgglomerativeClustering

  X = np.array([v for _, v in pairs], dtype=float)
  clusterer = AgglomerativeClustering(
      n_clusters=None,
      distance_threshold=dt,
      metric="cosine",
      linkage="average",
  )
  try:
    labels = clusterer.fit_predict(X)
  except Exception:
    logger.exception("Agglomerative clustering failed; returning singletons.")
    bucket = [concepts[i].get("name", "") for i, _ in pairs]
    return {"clusters": [], "miscellaneous": bucket}

  by_label: dict[int, list[int]] = {}
  for (orig_i, _), label in zip(pairs, labels):
    by_label.setdefault(int(label), []).append(orig_i)

  raw_clusters: list[dict] = []
  misc: list[str] = []
  for label, member_indices in by_label.items():
    members = [concepts[i] for i in member_indices]
    names = [m.get("name", "") for m in members]
    if len(members) < mc:
      misc.extend(names)
      continue
    # Rank exemplars by the optional ``frequency`` field if present
    # (built by the context graph), else preserve input order.
    exemplars = sorted(
        members,
        key=lambda m: -int(m.get("frequency", 0) or 0),
    )
    raw_clusters.append({
        "cluster_id": f"c{label}",
        "size": len(members),
        "exemplars": [m.get("name", "") for m in exemplars[:5]],
        "concepts": names,
        "total_frequency": sum(int(m.get("frequency", 0) or 0) for m in members),
    })

  # Rank clusters by combined frequency, keep top ``cap``.
  raw_clusters.sort(
      key=lambda c: (c["total_frequency"], c["size"]),
      reverse=True,
  )
  kept = raw_clusters[:cap]
  dropped = raw_clusters[cap:]
  for c in dropped:
    misc.extend(c["concepts"])

  return {
      "clusters": kept,
      "miscellaneous": sorted(set(misc)),
  }
