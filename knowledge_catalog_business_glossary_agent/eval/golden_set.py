"""Pydantic schemas + YAML loader for golden sets.

Golden YAML format is documented in eval/golden/<domain>/expected.yaml.
Loader substitutes ``${VAR}`` placeholders (typically ``${PROJECT}``)
before validating against the pydantic schema.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field


class MustLink(BaseModel):
  model_config = ConfigDict(extra="forbid")

  entry_suffix: str
  relationship: Literal["definition", "synonym", "related", "schema-join"]


class ExpectedTerm(BaseModel):
  model_config = ConfigDict(extra="forbid")

  display_name: str
  description: str
  expected_category: str
  aliases: List[str] = Field(default_factory=list)
  must_link: List[MustLink] = Field(default_factory=list)


class ExpectedCategory(BaseModel):
  model_config = ConfigDict(extra="forbid")

  display_name: str
  description: str
  aliases: List[str] = Field(default_factory=list)


class ExpectedGlossary(BaseModel):
  model_config = ConfigDict(extra="forbid")

  id_substrings: List[str]
  display_name_substring: str


class SecondaryPrompt(BaseModel):
  model_config = ConfigDict(extra="forbid")

  id: str
  mode: Literal["new", "extend", "extend-terms-only"]
  prompt: str
  target_glossary_id_substrings: Optional[List[str]] = None


class GoldenSet(BaseModel):
  model_config = ConfigDict(extra="forbid")

  domain: str
  description: str
  prompt: str
  mode: Literal["new", "extend", "extend-terms-only"]
  scope_hint: str
  gcs_uri: str
  bq_dataset: str
  catalog_queries: List[str]
  expected_glossary: Optional[ExpectedGlossary] = None
  expected_categories: List[ExpectedCategory] = Field(default_factory=list)
  expected_terms: List[ExpectedTerm] = Field(default_factory=list)
  secondary_prompts: List[SecondaryPrompt] = Field(default_factory=list)


_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _substitute(text: str, subs: Dict[str, str]) -> str:
  """Replaces ``${VAR}`` placeholders with values from ``subs``.

  Raises ValueError if any placeholder is missing — silent fall-through
  would yield garbage entry suffixes the harness would silently
  mis-match.
  """
  missing: list[str] = []

  def _repl(m: re.Match[str]) -> str:
    key = m.group(1)
    if key not in subs:
      missing.append(key)
      return m.group(0)
    return subs[key]

  result = _PLACEHOLDER_RE.sub(_repl, text)
  if missing:
    raise ValueError(
        f"Unsubstituted placeholders in golden YAML: {sorted(set(missing))}"
    )
  return result


def _substitute_recursive(obj, subs: Dict[str, str]):
  if isinstance(obj, str):
    return _substitute(obj, subs)
  if isinstance(obj, list):
    return [_substitute_recursive(x, subs) for x in obj]
  if isinstance(obj, dict):
    return {k: _substitute_recursive(v, subs) for k, v in obj.items()}
  return obj


def load_golden_set(
    path: str | os.PathLike,
    substitutions: Optional[Dict[str, str]] = None,
) -> GoldenSet:
  """Loads a golden YAML, substitutes placeholders, validates the schema."""
  raw = Path(path).read_text(encoding="utf-8")
  data = yaml.safe_load(raw)
  if substitutions:
    data = _substitute_recursive(data, substitutions)
  return GoldenSet.model_validate(data)


def discover_golden_sets(root: str | os.PathLike) -> List[Path]:
  """Returns all ``expected.yaml`` files under ``eval/golden/<domain>/``."""
  return sorted(Path(root).rglob("expected.yaml"))
