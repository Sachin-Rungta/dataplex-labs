"""Pydantic schemas for ontology + link recommender structured outputs.

These models give the LLM-driven sub-agents a strict shape to fill in and
let the root agent (and tests) validate that no entry name or evidence URI
was hallucinated. ADK already depends on pydantic v2, so this carries no
extra deps.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Ontology recommendation
# ---------------------------------------------------------------------------

class OntologyGlossary(BaseModel):
  """Identity of a recommended glossary (new-glossary mode only)."""

  model_config = ConfigDict(extra="forbid")

  id: str = Field(description="kebab-case glossary id, ≤ 40 chars")
  display_name: str
  description: str


class OntologyCategory(BaseModel):
  model_config = ConfigDict(extra="forbid")

  id: str
  display_name: str
  description: str
  parent_category_id: Optional[str] = None
  # When extending: True if this category already exists in the glossary
  # and the proposer wants to reuse it. The root agent will skip the
  # create_glossary_category call for these.
  existing: bool = False
  # Optional: concept names that seeded this category (for steward audit).
  seed_concepts: List[str] = Field(default_factory=list)


class OntologyTerm(BaseModel):
  model_config = ConfigDict(extra="forbid")

  id: str
  display_name: str
  category_id: Optional[str] = None
  description: str
  # Each evidence entry must be an entry resource name or a gs:// URI that
  # appears in the context graph or in the existing-glossary state.
  evidence: List[str]
  rationale: str
  # When extending: id of an existing term that this proposal is an alias
  # of (set when the steward should be asked to merge instead of create).
  aliases_existing_term_id: Optional[str] = None
  # Confidence the recommender assigns to this term, [0, 1].
  confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class OntologyRecommendation(BaseModel):
  model_config = ConfigDict(extra="forbid")

  mode: Literal["new", "extend"]
  # New-mode: glossary identity to create.
  glossary: Optional[OntologyGlossary] = None
  # Extend-mode: id of the existing glossary we are extending.
  glossary_id: Optional[str] = None
  glossary_location: Optional[str] = None
  categories: List[OntologyCategory] = Field(default_factory=list)
  terms: List[OntologyTerm] = Field(default_factory=list)
  truncated_at_terms: Optional[int] = None
  notes: Optional[str] = None
  # Set when dedup against an existing glossary found candidate-duplicates
  # the steward should review (not just silently drop).
  dedup_warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Link recommendation
# ---------------------------------------------------------------------------

# Canonical Dataplex EntryLinkType names. Keep aligned with
# tools/entry_links.py.
RelationshipType = Literal[
    "definition", "synonym", "related", "schema-join", "none",
]


class RelationshipVerdict(BaseModel):
  """Output of the LLM relationship classifier for a single (term, entry)
  candidate.
  """

  model_config = ConfigDict(extra="forbid")

  term_id: str
  target_entry_name: str
  relationship: RelationshipType
  confidence: float = Field(ge=0.0, le=1.0)
  justification: str


class RelationshipVerdictList(BaseModel):
  """Batched response shape used by ``classify_relationships``."""

  model_config = ConfigDict(extra="forbid")

  verdicts: List[RelationshipVerdict]


class LinkProposal(BaseModel):
  model_config = ConfigDict(extra="forbid")

  term_id: str
  term_display_name: str
  target_entry_name: str
  relationship: Literal["definition", "synonym", "related", "schema-join"]
  score: float
  cosine: Optional[float] = None
  rationale: str
  # When the link was discovered via lineage propagation, the upstream
  # entry that triggered it.
  derived_from_entry: Optional[str] = None
  # The classifier's verdict that produced this proposal (if any).
  classifier_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class LinkRecommendation(BaseModel):
  model_config = ConfigDict(extra="forbid")

  proposals: List[LinkProposal]
  skipped: List[Dict] = Field(default_factory=list)
  truncated_at: Optional[int] = None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_evidence(
    rec: OntologyRecommendation,
    allowed_entry_names: List[str],
    allowed_uris: List[str],
) -> List[str]:
  """Returns a list of violations: every term's evidence must reference an
  entry name or URI from the supplied allow-list. Used to catch the
  classic LLM failure mode of inventing entry resource names.
  """
  allowed = set(allowed_entry_names) | set(allowed_uris)
  violations: List[str] = []
  for term in rec.terms:
    if not term.evidence:
      violations.append(f"term '{term.id}' has no evidence")
      continue
    for ref in term.evidence:
      if ref not in allowed:
        violations.append(
            f"term '{term.id}' cites unknown evidence '{ref}'"
        )
  return violations


def validate_link_targets(
    rec: LinkRecommendation,
    allowed_entry_names: List[str],
) -> List[str]:
  """Every link proposal's target must be an entry seen in this turn's
  context graph.
  """
  allowed = set(allowed_entry_names)
  violations: List[str] = []
  for prop in rec.proposals:
    if prop.target_entry_name not in allowed:
      violations.append(
          f"link {prop.term_id} -> '{prop.target_entry_name}' references"
          " an unknown entry"
      )
  return violations
