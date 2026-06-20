# Knowledge Catalog Business Glossary Agent

An ADK-based agent that helps a data steward **design, maintain, and apply**
a business glossary inside **Dataplex Knowledge Catalog**. It grounds
recommendations in your real catalog entries and (optionally) your
unstructured docs in Google Cloud Storage, scores them with Vertex
embeddings + an LLM relationship classifier, and — with explicit
approval — creates, updates, and deletes glossary resources end-to-end.

It supports two recommendation paths:

- **New glossary** — propose identity, 3–10 categories, and 10–40 terms
  for a fresh domain.
- **Extend existing glossary** — fetch the glossary's current state,
  cosine-dedupe against existing terms, and propose only net-new
  categories/terms (plus alias suggestions for near-duplicates).

The agent never writes without explicit steward approval.

---

## What's inside

```
knowledge_catalog_business_glossary_agent/
├── setup.sh                     # one-shot bootstrap (APIs + IAM + DocAI + .env)
├── requirements.txt
├── README.md                    # this file
├── .env.example
├── docs/
│   ├── DESIGN.md                # full PRD / roadmap (V1 → V3)
│   └── eval/README.md           # eval harness layout (stub)
└── knowledge_catalog_business_glossary_agent/
    ├── agent.py                 # root ADK agent
    ├── config.py
    ├── utils.py
    ├── prompts/
    │   ├── root_instructions.md
    │   ├── ingestion_agent.md
    │   ├── ontology_recommender.md
    │   └── link_recommender.md
    ├── sub_agents/
    │   ├── ingestion_agent.py
    │   ├── ontology_agent.py
    │   └── link_agent.py
    └── tools/
        ├── catalog_search.py            # KC SearchEntries + LookupContext (cached)
        ├── context_graph.py             # concept + co-occurrence graph
        ├── gcs_ingest.py                # GCS doc reader (text + DocAI router)
        ├── documentai_ingest.py         # Layout Parser / OCR
        ├── embeddings.py                # Vertex text-embedding-005 + cache
        ├── clustering.py                # sklearn agglomerative cluster → category seeds
        ├── semantic_ontology.py         # embedding-based scorers, link candidate ranking
        ├── ontology.py                  # legacy lexical scorers (kept for fallback)
        ├── link_classifier.py           # batched Gemini relationship classifier
        ├── lineage.py                   # Data Lineage neighbour expansion
        ├── glossary_state.py            # read existing glossary + bulk cosine dedup
        ├── glossary_crud.py             # glossary / category / term CRUD
        ├── entry_links.py               # EntryLink CRUD
        └── schemas.py                   # pydantic v2 output schemas + validators
```

---

## Architecture (one-paragraph)

The root agent reads the steward's intent, then delegates: the **ingestion
agent** builds a co-occurrence context graph from Knowledge Catalog
entries and any GCS docs you point at it, then warms a per-process
Vertex embedding cache. The **ontology agent** clusters concepts into
category seeds, scores term candidates with `0.4 · lexical + 0.6 ·
cosine-to-domain-centroid`, and either proposes a new glossary or
dedupes against an existing one. The **link agent** ranks candidate
(term, entry) pairs by cosine, sends the survivors through a batched
LLM relationship classifier that picks `definition` / `synonym` /
`related` / `schema-join` (or drops the pair as `none`), and — when
lineage is enabled — expands strong-definition links to upstream and
downstream entries as `related`. CRUD only runs after explicit
approval.

The full design + roadmap (V1 → V3) is in [`docs/DESIGN.md`](docs/DESIGN.md).

---

## One-shot setup

You'll need a single GCP project and a recent `gcloud` SDK installed
locally. From this directory:

```bash
./setup.sh --project=my-gcp-project
```

That command (idempotently) does all six setup steps:

1. Enables `dataplex`, `aiplatform`, `storage`, `documentai`, and
   `serviceusage` APIs on the project.
2. Detects your active `gcloud` identity and grants:
   `roles/dataplex.editor`, `roles/dataplex.viewer`,
   `roles/aiplatform.user`, `roles/documentai.apiUser`,
   `roles/storage.objectViewer`, `roles/serviceusage.serviceUsageConsumer`.
3. Creates (or reuses) a **Document AI Layout Parser** processor named
   `glossary-layout-parser` in `--docai-location` (default `us`).
4. Writes a fully-populated `.env` (backs up any existing one).
5. Creates a virtualenv in `.venv/` and installs `requirements.txt`.
6. Prints how to finish ADC and start the agent.

### Useful flags

| Flag | What it does |
| --- | --- |
| `--project=<id>` | GCP project (otherwise `$GOOGLE_CLOUD_PROJECT`). |
| `--principal=<user:foo@x|serviceAccount:...>` | Override the IAM principal (default: active gcloud account). |
| `--vertex-location=us-central1` | Region for embeddings + classifier calls. |
| `--gcs-bucket=<bucket>` | Grant `objectViewer` only on this bucket (else project-wide). |
| `--enable-lineage` | Enable Data Lineage API + role + lineage env flag. |
| `--skip-docai` | Don't create the DocAI processor. |
| `--skip-iam` | Don't grant roles (you've already done it). |
| `--skip-apis` | Don't enable APIs. |
| `--skip-venv` / `--skip-install` | Skip Python env setup. |
| `--skip-env` | Don't (re)write `.env`. |

After setup finishes:

```bash
gcloud auth application-default login           # one-time
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
adk run .
```

---

## Manual setup (alternative)

If you'd rather not run the script, the equivalent manual steps:

```bash
# 1. Enable APIs
gcloud services enable \
    dataplex.googleapis.com \
    aiplatform.googleapis.com \
    storage.googleapis.com \
    documentai.googleapis.com \
    serviceusage.googleapis.com \
    --project=$GOOGLE_CLOUD_PROJECT
# Optional: + datalineage.googleapis.com

# 2. IAM (replace user:you@example.com)
for r in dataplex.editor dataplex.viewer aiplatform.user \
         documentai.apiUser storage.objectViewer \
         serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \
      --member="user:you@example.com" \
      --role="roles/$r" --condition=None
done

# 3. Document AI processor
gcloud documentai processors create \
    --location=us \
    --display-name=glossary-layout-parser \
    --type=LAYOUT_PARSER_PROCESSOR

# 4. Python deps
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 5. .env from template
cp .env.example .env  # then edit
export $(grep -v '^#' .env | xargs)

# 6. ADC
gcloud auth application-default login
adk run .
```

---

## Using the agent

`adk run .` drops you into a REPL with the root agent. Talk to it like a
data steward asking another steward for help. Two example sessions
follow.

### Session A — recommend a brand-new glossary

```
> Recommend a glossary for our customer-360 domain in project
  acme-data-prod. Use docs at gs://acme-data-docs/customer-wiki/ as
  context. Focus on business concepts (e.g. "Active Customer"), not
  column-name fragments.
```

What happens behind the scenes:

1. Root agent extracts: `project_id=acme-data-prod`, `gcs_uri=gs://...`,
   `scope_hint="customer 360"`, `style_guidance="business concepts..."`.
2. **Ingestion agent** runs Knowledge Catalog search with 3–5 derived
   queries, reads the GCS wiki (routing PDFs through DocAI), builds the
   context graph, and warms the embedding cache.
3. **Ontology agent** (mode = `new`) clusters the concepts, names
   categories with the LLM, scores term candidates, and returns a
   structured `OntologyRecommendation`.
4. The root agent renders categories + terms with citations and asks
   you to **approve all / approve a subset / revise**.
5. On approval, it calls `create_glossary` → `create_glossary_category`
   (n) → `create_glossary_term` (n).
6. **Link agent** then runs cosine + classifier (+ optional lineage),
   shows you the proposals grouped by term, and creates EntryLinks on
   approval.

### Session B — extend an existing glossary

```
> Add new terms to our existing 'customer-360' glossary. Use
  gs://acme-data-docs/customer-wiki/ plus our latest billing schema
  in project acme-data-prod (system=bigquery). Drop anything that
  duplicates terms we already have, but flag near-duplicates for me
  to merge.
```

What happens:

1. Root agent picks Path B because the steward named an existing
   glossary id.
2. **Ingestion agent** runs (same as Session A).
3. **Ontology agent** (mode = `extend`):
   a. Calls `get_existing_glossary_state("customer-360")` — pulls all
      current categories + terms.
   b. Builds candidate terms from the new graph.
   c. Calls `find_similar_existing_terms_bulk` — for each candidate,
      finds the closest existing term by cosine.
   d. Buckets candidates: drop (cosine ≥ 0.78), alias-merge candidate
      (0.65 – 0.78), or net-new (< 0.65).
   e. Returns only net-new categories/terms + the alias warnings.
4. Root agent renders a **diff** view: NEW / REUSED / NEW TERMS /
   POSSIBLE ALIASES / DROPPED AS DUPLICATES.
5. You decide per-alias (Skip / Merge / Create anyway).
6. Approved net-new categories + terms are created against the existing
   glossary. Then the link agent runs (optionally scoped to *just* the
   new terms).

### Other things you can ask

```
# Just ingestion (skip recommendations)
> What's in gs://acme-data-docs/marketing/? Don't recommend anything
  yet — I just want to see what concepts are there.

# Just link recommendations against an existing glossary
> Propose new links for the existing 'customer-360' glossary against
  the billing dataset (project acme-data-prod, dataset=billing).

# Dry inspect of existing glossary
> List our glossaries and show what's in 'customer-360'.

# Bulk delete (always asks for confirmation by name)
> Delete these orphan terms from 'customer-360': X, Y, Z.
```

---

## Configuration reference

All knobs live in env vars. The `setup.sh` script generates a sensible
`.env`; tune as needed.

### Core

| Var | Default | Notes |
| --- | --- | --- |
| `GOOGLE_CLOUD_PROJECT` | _(required)_ | Consumer / billing project. |
| `GOOGLE_GENAI_USE_VERTEXAI` | `True` | Use Vertex AI for Gemini. |
| `VERTEX_LOCATION` | `us-central1` | Region for embeddings + classifier. |
| `DATAPLEX_GLOSSARY_LOCATION` | `global` | Where glossaries get created. |
| `DATAPLEX_API_ENDPOINT` | `dataplex.googleapis.com` | |

### Models

| Var | Default | Notes |
| --- | --- | --- |
| `GLOSSARY_AGENT_MODEL` | `gemini-3-flash-preview` | Driver model for every agent in the tree. |
| `GLOSSARY_AGENT_CLASSIFIER_MODEL` | `gemini-3-flash-preview` | Relationship-classifier model. Override to use a stronger model for higher link F1. |
| `GLOSSARY_AGENT_EMBEDDING_MODEL` | `text-embedding-005` | Vertex text embedding model. |
| `GLOSSARY_AGENT_EMBEDDING_DIM` | `0` | `0` → model default (768 for v5). |
| `GLOSSARY_AGENT_EMBEDDING_BATCH` | `100` | Texts per Vertex embedding request. |

### Ingestion

| Var | Default | Notes |
| --- | --- | --- |
| `GLOSSARY_AGENT_MAX_GCS_DOCS` | `50` | Cap on docs read per turn. |
| `GLOSSARY_AGENT_MAX_DOC_BYTES` | `524288` | Per-doc byte cap (text). Binary docs get 8×. |

### Document AI

| Var | Default | Notes |
| --- | --- | --- |
| `DOCUMENT_AI_LOCATION` | `us` | DocAI region. |
| `DOCUMENT_AI_PROCESSOR_ID` | _(empty)_ | Processor UUID — empty disables DocAI; PDFs etc. are silently skipped. |
| `DOCUMENT_AI_PROCESSOR_VERSION` | _(empty)_ | Optional pinned processor version. |

### Recommendation thresholds

| Var | Default | Notes |
| --- | --- | --- |
| `GLOSSARY_AGENT_LINK_COSINE_MIN` | `0.45` | Drop (term, entry) pairs below this cosine before the classifier runs. |
| `GLOSSARY_AGENT_LINK_COSINE_STRONG` | `0.72` | At/above this cosine, a `definition` link triggers lineage propagation. |
| `GLOSSARY_AGENT_DEDUP_COSINE` | `0.78` | When extending, candidates within this cosine of an existing term are dropped as duplicates. |
| `GLOSSARY_AGENT_CLUSTER_DISTANCE` | `0.55` | Cosine distance threshold for AgglomerativeClustering. Lower → tighter, more clusters. |
| `GLOSSARY_AGENT_MIN_CLUSTER_SIZE` | `3` | Clusters smaller than this become "miscellaneous". |
| `GLOSSARY_AGENT_MAX_CATEGORIES` | `10` | PRD cap. |
| `GLOSSARY_AGENT_MAX_TERMS` | `40` | PRD cap. |
| `GLOSSARY_AGENT_MAX_CLASSIFIER_PAIRS` | `200` | Hard cap on (term, entry) pairs sent to the classifier per turn. |

### Data Lineage (opt-in)

| Var | Default | Notes |
| --- | --- | --- |
| `GLOSSARY_AGENT_USE_LINEAGE` | `false` | When `true`, the link agent expands strong-definition links into `related` proposals for upstream/downstream entries. |
| `LINEAGE_LOCATION` | `us` | Data Lineage region. |
| `LINEAGE_MAX_HOPS` | `1` | BFS depth. |
| `LINEAGE_MAX_NEIGHBORS` | `25` | Cap per direction per seed. |

Hard-fail policy: **once a flag is set, the corresponding subsystem is
required to succeed**. If Vertex is unreachable, the embedding call
raises. If lineage is enabled but the API is unauthorized, lineage
returns a structured error rather than silently falling back. Leave
the env var unset (empty `DOCUMENT_AI_PROCESSOR_ID`,
`GLOSSARY_AGENT_USE_LINEAGE=false`) to disable a subsystem cleanly.

---

## Safety model

- Recommendations are produced before any write call.
- Every `create_*` / `update_*` / `delete_*` call requires an explicit
  in-conversation approval naming the change.
- Term `evidence` and link `target_entry_name` are validated against the
  ingestion graph — the agent will refuse to write a link to an entry
  it never saw.
- Tool errors are surfaced verbatim; the agent stops a destructive
  sequence and asks how to proceed rather than swallow failures.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `ValueError: GOOGLE_CLOUD_PROJECT environment variable is required.` | `.env` not loaded into the shell. | `export $(grep -v '^#' .env | xargs)` before `adk run .`. |
| `403 Permission denied calling Knowledge Catalog Search.` | IAM not granted. | Re-run `./setup.sh --skip-apis --skip-docai --skip-venv --skip-install` to re-apply IAM. |
| Embedding call hangs / 401 | ADC missing or expired. | `gcloud auth application-default login`. |
| Binary docs all show `status: skipped`. | `DOCUMENT_AI_PROCESSOR_ID` empty. | Re-run setup without `--skip-docai`, or create a processor manually and paste the id. |
| Lineage tool returns `enabled: false`. | `GLOSSARY_AGENT_USE_LINEAGE` not `true`. | Set it; ensure `datalineage.googleapis.com` is on. |
| Classifier returns malformed output. | Model not supporting `response_schema`. | Override `GLOSSARY_AGENT_CLASSIFIER_MODEL` to a Gemini-2.5+ id. |
| `RESOURCE_EXHAUSTED` from Vertex. | Embedding QPS or token quota hit. | Lower `GLOSSARY_AGENT_EMBEDDING_BATCH`; request quota. |
| Recommendations feel noisy. | Domain centroid too broad. | Provide a tighter `scope_hint` in your message, or raise `GLOSSARY_AGENT_LINK_COSINE_MIN`. |
| Recommendations feel sparse. | Cosine threshold too strict. | Lower `GLOSSARY_AGENT_LINK_COSINE_MIN`; lower `GLOSSARY_AGENT_DEDUP_COSINE`. |

---

## References

- [Knowledge Catalog Search API](https://cloud.google.com/dataplex/docs/reference/rest/v1/projects.locations/searchEntries)
- [Business Glossary REST API](https://cloud.google.com/dataplex/docs/reference/rest/v1/projects.locations.glossaries)
- [EntryLinks API](https://cloud.google.com/dataplex/docs/reference/rest/v1/projects.locations.entryGroups.entryLinks)
- [Document AI — Layout Parser](https://cloud.google.com/document-ai/docs/layout-parse-chunk)
- [Data Lineage API](https://cloud.google.com/data-catalog/docs/reference/data-lineage/rest)
- [Vertex AI text embeddings](https://cloud.google.com/vertex-ai/generative-ai/docs/embeddings/get-text-embeddings)
- [ADK Documentation](https://adk.dev/get-started/python/)
- Full roadmap: [`docs/DESIGN.md`](docs/DESIGN.md)
