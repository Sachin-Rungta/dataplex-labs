# Business Glossary Agent — Full Design & Work Plan

> Living design document. Owner: Glossary Agent team. Audience: PM, eng,
> data stewards, conversational-analytics partner team.

---

## 1. North Star

> A data steward describes a domain (or points at a folder of docs and a
> project) and within minutes has a **published, governed business glossary
> in Dataplex Knowledge Catalog** that is **linked to the right assets**,
> and that **measurably improves the quality of conversational analytics**
> answers on top of those assets.

Two outcomes, measured separately:

| Outcome | Primary metric | Bar to ship V1 |
| --- | --- | --- |
| Glossary quality | Steward acceptance rate of recommended terms / links | ≥ 70% term acceptance, ≥ 60% link acceptance @ steward review |
| Conversational analytics uplift | Δ answer-correctness on a held-out NL-to-SQL question set | ≥ +15pp absolute on ambiguous/business-term questions vs no-glossary control |

If we hit ontology bar but not uplift, we shipped a pretty taxonomy that
nobody uses. If we hit uplift but not acceptance, stewards won't trust the
tool and won't run it.

---

## 2. Product Pillars

P1. **Context grounding** — assemble the evidence (catalog + docs + lineage)
    before any recommendation.
P2. **Ontology recommendation** — propose glossary identity, categories,
    terms with definitions and citations.
P3. **Link recommendation** — propose `definition` / `synonym` / `related`
    / `schema-join` EntryLinks (canonical Dataplex EntryLinkType names)
    between terms and catalog entries.
P4. **Lifecycle** — recommend → review → apply (create/update/delete) with
    auditability and rollback.
P5. **Conversational-analytics uplift** — prove that the glossary + links
    improve downstream NL-to-SQL / NL-to-answer quality.

Each pillar has a current implementation (V1, the scaffold already in
this repo) and **two upgrade paths** below, sized small / medium / large.

---

## 3. Architecture evolution

### V1 (shipped scaffold)
- ADK root agent + 3 sub-agents (ingestion, ontology, link).
- Context graph built from KC `SearchEntries` + GCS docs via token
  co-occurrence. Plain-text formats read directly; **PDFs, scanned images,
  DOCX / PPTX / XLSX, and HTML routed through Document AI** (Layout Parser
  recommended). DocAI is opt-in via env var; agent degrades gracefully
  when it's off (binary docs are skipped with a steward-facing warning).
- Lexical scorer (token overlap + name-hit bonus) for link candidates.
- Full CRUD via Dataplex REST API; recommend-then-apply with explicit
  steward approval.

### V2 (semantic core — ~4-6 weeks)
- Replace token tokenization with **embeddings** (Vertex AI text-embedding-005)
  for both concepts and entries.
- Cluster terms with HDBSCAN / agglomerative; categories from cluster
  centroids.
- Replace link lexical score with cosine similarity over embeddings +
  LLM verifier ("does term X describe entry Y? answer with relationship
  type + one-sentence justification or NONE").
- Upgrade DocAI integration: per-chunk embeddings (Layout Parser already
  emits chunks in V1), table extraction routed to a dedicated parser.
- Add **lineage** signal (Data Lineage API): if entry A is upstream of
  entry B and B is linked to term T, propose `related` link for A.

### V3 (governance + uplift loop — ~6-10 weeks after V2)
- **Glossary versioning**: every apply creates a version snapshot; diff
  view; one-click rollback.
- **Feedback loop**: steward accept/reject/edit is captured and fed back
  as few-shot examples or fine-tuning data.
- **Uplift experiment harness**: A/B framework running a downstream NL-to-SQL
  agent with and without the glossary in context, scoring with the eval
  rubric in §6.
- **Multi-glossary federation**: detect overlap between glossaries (e.g.
  Marketing vs Sales both define "Customer") and propose reconciliation.
- **Aspect-type aware**: attach term hints into Dataplex aspect types so
  downstream agents pick them up automatically.

---

## 4. Approaches per pillar (tradeoffs)

### P1. Context grounding

| Approach | Pros | Cons | When |
| --- | --- | --- | --- |
| **A. KC search only** | Cheap, fast, no extra infra | Misses business context that lives in docs | Smoke-test / demo |
| **B. KC + GCS text** | Adds steward-authored context | Token-based; weak on synonyms; PDF blind | Fallback when DocAI not configured |
| **C. KC + GCS + Document AI** (current V1) | Handles PDFs, scanned policy docs, Office files, tables | DocAI cost + latency per binary doc | **V1 default** when processor configured |
| **D. C + Lineage + Profile** | Picks up upstream/downstream context and column stats | Most signals to fuse; needs ranking | V2+ for high-value domains |
| **E. D + BigQuery sample rows** | Strongest grounding for ambiguous columns | Privacy review needed; sampling cost | Opt-in per domain |

**Recommendation:** **C in V1** (Layout Parser processor; degrades to B
automatically when the processor env var is unset). D in V2 once
embeddings land; E gated per-project for privacy review.

**Implementation notes (V1):**
- Processor type defaults to **Layout Parser** because it preserves block /
  heading structure that helps the ontology agent distinguish definitions
  from incidental prose. OCR is the cheaper fallback for PDF-only corpora.
- Per-doc results carry a `status` of `ok | skipped | error` so the agent
  can tell the steward exactly which docs contributed signal.
- Binary docs are capped at 8× the text byte cap (`GLOSSARY_AGENT_MAX_DOC_BYTES`)
  to allow for the larger payloads without unbounded growth.

### P2. Ontology recommendation

| Approach | Pros | Cons | When |
| --- | --- | --- | --- |
| **A. Pure LLM zero-shot** | Easy to build | Hallucinates terms, no citations | Never as primary |
| **B. Token graph + LLM promotion** (current) | Citable, deterministic concepts | Misses synonyms, weak hierarchy | V1 |
| **C. Embedding clusters + LLM naming** | Catches synonyms, cleaner categories | Needs embeddings infra | V2 |
| **D. C + RAG over steward's existing glossaries** | Reuses org conventions | Needs corpus of prior glossaries | V2.5 |
| **E. D + structured taxonomy prior** (FIBO, schema.org, industry models) | Strong defaults for known industries | Mapping effort per industry | V3 / vertical packs |

**Recommendation:** B → C → D as the spine. Industry priors (E) as optional
"templates" the steward can opt into.

### P3. Link recommendation

| Approach | Pros | Cons | When |
| --- | --- | --- | --- |
| **A. Lexical overlap** (current) | No external calls | Misses semantic matches; brittle on column name fragments | V1 |
| **B. Embedding cosine + threshold** | Catches synonyms (`cust_id` ↔ `Customer`) | Needs threshold tuning per domain | V2 |
| **C. B + LLM relationship classifier** | Picks `definition` vs `synonym` vs `related` vs `schema-join` correctly | LLM cost per candidate pair | V2 |
| **D. C + lineage-propagation** | Auto-links downstream tables when an upstream is linked | Needs careful confidence decay | V2.5 |
| **E. D + steward-in-the-loop active learning** | Improves over time per org | Needs feedback storage + retraining | V3 |

**Recommendation:** B+C in V2 — the relationship picker is what makes
links actually useful for downstream agents.

### P4. Lifecycle

| Approach | Pros | Cons | When |
| --- | --- | --- | --- |
| **A. Recommend-then-CRUD** (current) | Simple, auditable in chat | No version history outside chat log | V1 |
| **B. A + glossary-as-code export** (YAML/Sheet) | Steward can edit offline, diff in PR | Two systems of record | V2 |
| **C. B + version snapshots in Firestore** (policy-as-code pattern) | Real audit trail, rollback | Extra infra | V3 |
| **D. C + scheduled drift detection** | Catches manual changes to glossary | Cron infra, alerting | V3 |

**Recommendation:** A in V1; add B in V2 (reuses the existing
`business-glossary-import` Sheets/YAML pipeline already in this repo).

### P5. Conversational-analytics uplift

This is the differentiator. Approaches differ in *how the glossary is
exposed to the downstream agent*:

| Approach | Mechanism | Pros | Cons |
| --- | --- | --- | --- |
| **A. Prompt-stuffing** | Inject relevant terms + definitions into the NL-to-SQL agent's system prompt | Trivial to ship | Token-heavy, doesn't scale past ~50 terms |
| **B. Retrieval-augmented** | At query time, retrieve top-K terms whose definitions match the user question | Scales to large glossaries | Needs an embedding index of glossary terms |
| **C. EntryLink-driven** | When the agent resolves a table, follow EntryLinks to attach term context | Uses Dataplex as the index | Requires links to be high quality |
| **D. Hybrid B+C** | RAG for term disambiguation + EntryLink walk for asset-grounded definitions | Best of both | Most plumbing |

**Recommendation for the uplift experiment:** ship **D** in the
downstream NL-to-SQL agent and measure against a no-glossary control.

---

## 5. Phased delivery plan

### Phase 0 — Baseline (week 0, ~now)
- ✅ V1 scaffold committed (this PR).
- Set up a project with a known small catalog (≤ 200 entries) as the
  fixed dev environment.
- Write the eval golden set v0 (§6).

### Phase 1 — V1 hardening (weeks 1–3)
| # | Task | Owner | Definition of done |
|---|------|-------|--------------------|
| 1.1 | Wire ADK eval harness and run V1 against golden set | Eng | Numbers in CI for every PR |
| 1.2 | Add unit tests for `glossary_crud`, `entry_links`, `gcs_ingest` (mock HTTP) | Eng | ≥ 80% line coverage in `tools/` |
| 1.3 | Integration test against a sandbox project (create → recommend → apply → delete) | Eng | Green nightly run |
| 1.4 | Steward UX pass on recommendation rendering | PM + DS | Usability test with 3 stewards |
| 1.5 | Telemetry: log every recommendation + accept/reject decision to BQ | Eng | Dashboard with funnel |
| 1.6 | Threat model + permissions doc (Datalpex IAM, GCS IAM, ADC scope) | Eng | Security review sign-off |

### Phase 2 — Semantic core (weeks 4–8)
| # | Task | Owner | DoD |
|---|------|-------|-----|
| 2.1 | Add Vertex embedding tool; build embedding index for concepts + entries | Eng | Index rebuilt on demand |
| 2.2 | Swap token graph → embedding clusters for ontology | Eng | Eval scores ≥ V1 baseline + 10pp on coverage |
| 2.3 | Swap lexical scorer → cosine + LLM relationship classifier for links | Eng | Eval link-F1 ≥ V1 + 15pp |
| 2.4 | DocAI hardening: chunk-level embeddings, table-aware routing, per-processor benchmarks | Eng | Layout Parser vs OCR comparison on golden set |
| 2.5 | Add Lineage signal to link scorer | Eng | Lineage signal contributes ≥ 10% of accepted links in sample |
| 2.6 | Glossary-as-code export (YAML) | Eng | Round-trip: export → edit → re-import |

### Phase 3 — Uplift experiment (weeks 6–10, overlaps phase 2)
| # | Task | Owner | DoD |
|---|------|-------|-----|
| 3.1 | Choose downstream NL-to-SQL agent (could be `data-mesh-banking-labs` or new) | PM | Decision doc |
| 3.2 | Build glossary-aware context module (approach 5.D) for the downstream agent | Eng | Behind a flag |
| 3.3 | Author NL question golden set (§6.4) with expected SQL / answer | Eng + DS | 50–100 questions, peer-reviewed |
| 3.4 | Run A/B: glossary OFF vs ON, score with rubric | Eng | Report with significance test |
| 3.5 | If positive: write public case study with metrics | PM | Blog draft |

### Phase 4 — Governance + scale (weeks 10–14)
| # | Task | Owner | DoD |
|---|------|-------|-----|
| 4.1 | Glossary version snapshots in Firestore + diff view | Eng | Rollback works end-to-end |
| 4.2 | Steward feedback capture → few-shot store | Eng | Last-30-day feedback influences next run |
| 4.3 | Multi-glossary overlap detection + merge proposal | Eng | Demo on 2 overlapping domains |
| 4.4 | Drift detector cron + Slack alert | Eng | Manual-edit detected within 1 hour |
| 4.5 | Vertical pack: FIBO (finance) ontology prior | PM | Optional template selectable in CLI |

---

## 6. Evaluation framework

### 6.1 Eval philosophy
- **Golden sets, not vibes.** Every pillar gets a frozen golden set
  reviewed by ≥ 2 humans. Score in CI; regressions block merges.
- **Realistic surface.** Use real Dataplex projects (sanitized) and real
  steward docs, not synthetic catalogs.
- **Two-tier scoring**: cheap automatic metrics on every PR; expensive
  human-rubric eval weekly.

### 6.2 Golden sets to author (Phase 1)

| Set | Size | Inputs | Labels |
|-----|------|--------|--------|
| `gs_ontology_v0` | 3 domains × ~30 terms | Project + GCS docs per domain | Expert glossary (terms, categories, definitions) |
| `gs_links_v0` | ~500 (term, entry) pairs | Same 3 domains | `definition` / `synonym` / `related` / `schema-join` / `none` |
| `gs_ingestion_v0` | 20 GCS docs | PDF, MD, CSV mix | Expected extracted concept list |
| `gs_nl_questions_v0` | 50–100 NL questions per domain | Questions stewards/analysts actually ask | Expected SQL or expected answer + reference tables |

### 6.3 Metrics per pillar

**P2 — Ontology**
- *Term precision @K* — fraction of top-K recommended terms accepted by
  expert. Target: P@20 ≥ 0.75 in V2.
- *Term recall vs expert glossary* — fraction of expert terms surfaced
  (even if ranked lower). Target: R ≥ 0.80.
- *Category coherence* — human rubric (1–5) on whether terms grouped
  under a category belong together. Target: mean ≥ 4.0.
- *Definition usefulness* — human rubric (1–5) on whether definition is
  accurate and non-tautological. Target: mean ≥ 4.0.
- *Time-to-glossary* — wall-clock from start to first apply. Target:
  < 10 min for a domain of 200 entries.

**P3 — Links**
- *Link precision* — fraction of agent-proposed links accepted. Target:
  P ≥ 0.70 in V2.
- *Link recall* — vs expert link set. Target: R ≥ 0.60 (recall is harder
  because experts under-link too).
- *Relationship-type accuracy* — given link is correct, was the
  EntryLinkType (`definition` / `synonym` / `related` / `schema-join`)
  chosen correctly. Target: ≥ 0.85.
- *F1* — harmonic mean.

**P1 — Ingestion**
- *Concept recall* — fraction of expert-labeled concepts the ingestion
  agent surfaces in the context graph. Target: R ≥ 0.85.
- *Noise rate* — fraction of surfaced concepts that are clearly noise.
  Target: ≤ 0.20.

**P5 — Uplift** (the headline)
- *Answer correctness* (binary, human-judged) on `gs_nl_questions_v0`,
  for: (a) no glossary, (b) glossary terms only, (c) glossary + links.
  **Target: Δ(c, a) ≥ +15pp absolute on the "ambiguous" question subset.**
- *Schema-resolution accuracy* — for NL-to-SQL: did the agent join the
  right tables / pick the right columns? Target: +20pp on ambiguous subset.
- *Hallucination rate* — fraction of answers citing a column / table that
  doesn't exist. Target: glossary version ≤ baseline.
- *Latency overhead* — added ms per query when glossary is on. Target:
  ≤ 300 ms p95.

### 6.4 Uplift experiment design

```
Population:    NL questions in gs_nl_questions_v0 (held out from training).
Stratify by:   {direct schema match, ambiguous business term, multi-table}
Arms:          A = no glossary
               B = glossary terms only in prompt (approach 5.A)
               C = RAG term lookup + EntryLink walk (approach 5.D)
Sample size:   ≥ 80 questions per stratum (power for ±10pp at 80% confidence)
Scoring:       Two human raters per (question, arm) answer; Cohen's κ ≥ 0.7
               required before reporting. Disagreements adjudicated by a
               third rater.
Reporting:     Per-stratum effect size + 95% CI; bootstrap for significance.
```

A positive result requires: arm C beats arm A by ≥ 15pp on the
"ambiguous business term" stratum at p < 0.05. Arm B is the cheap-baseline
check — if B already wins, the EntryLink machinery may be over-engineered.

### 6.5 Test harness

- **ADK eval** for agent-level scoring (in `eval/`).
- **pytest + responses** for tool unit tests (mock the Dataplex REST API).
- **Nightly integration** in a sandbox GCP project (`glossary-agent-ci`)
  that creates / tears down a real glossary.
- **Weekly human eval session** (60 min) on a sample of the golden set —
  rotate among 3 stewards to keep the labels honest.

---

## 7. Conversational-analytics uplift story

### 7.1 The hypothesis (one paragraph for the deck)
> "Conversational analytics agents fail most often on questions whose
> terms don't match the schema: `revenue` vs `gross_billed_amount_usd`,
> `active customer` vs `cust_status='A'`. A glossary that maps business
> terms to columns/tables, plus EntryLinks that tell the agent *which*
> assets to look at for a term, removes that ambiguity. We expect a
> 15–30pp lift on the ambiguous question stratum, with no regression on
> direct-schema questions."

### 7.2 Demo script (for stakeholder reviews)
1. Pick an analyst question: *"What was monthly active customer revenue
   in Q1 for the EMEA region?"*
2. **Without glossary**: agent guesses; picks `customers` table (wrong —
   it's `cust_dim_active_v3`); produces SQL referencing `revenue` (no
   such column); returns wrong answer or "I don't know".
3. **With glossary**: agent resolves "active customer" → term `Active
   Customer` → linked to `cust_dim_active_v3`; resolves "revenue" →
   term `Net Revenue` → linked to `fact_billing.net_amt_usd`; produces
   correct SQL; returns answer with citations to the glossary terms.
4. Side-by-side metrics from the A/B run.

### 7.3 Why this matters beyond a demo
Every conversational analytics product is going to claim the same lift.
What's defensible here is: the lift comes from a glossary the customer
*owns and governs in their own catalog*, built with low effort by an
agent. That's a moat for Dataplex Knowledge Catalog, not a moat for any
one chatbot.

---

## 8. Open questions for the team

1. **Embedding model:** text-embedding-005 vs Gemini-embedding-001 vs
   text-multilingual-embedding-002 for non-English glossaries?
2. **Where does the glossary embedding index live?** Vertex Vector Search
   (managed but expensive) vs BigQuery vector search vs in-process FAISS?
3. **Steward authentication for write actions:** rely on ADC of the
   running user, or add a per-action confirmation step that writes the
   user's identity into the audit log?
4. **Downstream agent partner:** which existing NL-to-SQL agent do we
   integrate for the uplift experiment? Banking-labs? A new minimal one
   we build for the experiment?
5. **Industry priors:** which vertical first — finance (FIBO), retail
   (GS1), healthcare (FHIR)?
6. **Failure-mode reporting:** when the agent's recommendation is bad,
   what's the structured feedback the steward gives so we can learn?
7. **Cost ceiling per recommendation run:** what's the budget per
   steward-session? (Drives choice of embedding vs LLM verifier calls.)
8. **PII handling for GCS docs:** scan with DLP before ingestion?

---

## 9. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Stewards reject recommendations as too noisy | High | Kills adoption | Eval-first; show evidence per term; allow per-term reject in UI |
| Apply step writes wrong resources | Medium | Trust killer | Dry-run mode; version snapshots; rollback path |
| GCS docs contain PII | Medium | Compliance | DLP pre-scan; redact before LLM |
| Uplift experiment shows no lift | Medium | Strategy hit | Pre-register hypothesis; have B-line approach (prompt-stuff) as fallback story |
| LLM hallucinates entry names | Low | Bad links | Hard rule: every proposed link must reference an entry seen in this turn's context graph; CI test enforces |
| Dataplex glossary API changes | Low | Maintenance | Pin SDK; integration test nightly |

---

## 10. Appendix: file-level task breakdown for Phase 1

| File | Action | Notes |
|------|--------|-------|
| `tools/glossary_crud.py` | Add `eval-mode dry_run=True` flag | Returns body it would send, doesn't POST |
| `tools/entry_links.py` | Add `dry_run` flag | Same |
| `tools/catalog_search.py` | Add caching layer keyed by `(query, project)` | Avoid repeated SearchEntries calls in a single turn |
| `tools/context_graph.py` | Pluggable tokenizer → vectorizer interface | Prepares for Phase 2 swap |
| `sub_agents/ontology_agent.py` | Add structured output validation (pydantic) | Reject hallucinated entry names |
| `eval/` (new) | ADK eval configs + golden set fixtures | One subdir per pillar |
| `tests/` (new) | pytest suites for each tool | Mock requests; mock Dataplex SDK |
| `docs/RUNBOOK.md` (new) | On-call runbook | What to do when nightly integration breaks |
| `.github/workflows/eval.yml` (new) | Run eval on every PR | Block merge on regression |

---

## 11. Definition of done for the whole program

We declare success when:
1. A new steward can produce an approved 30-term glossary for a fresh
   domain in **under 15 minutes**, with **≥ 70% of recommendations
   accepted as-is**.
2. The conversational analytics uplift study published, with **≥ +15pp**
   on the ambiguous-question stratum, **p < 0.05**.
3. Two production customers have run the agent on real domains and
   re-engaged within 30 days.
4. The agent is the default "Generate Glossary" entry point in the
   Dataplex Knowledge Catalog console (post-V2).
