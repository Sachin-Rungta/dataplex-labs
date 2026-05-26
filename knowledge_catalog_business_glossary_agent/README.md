# Knowledge Catalog Business Glossary Agent

## About

The Business Glossary Agent helps a data steward design, maintain, and apply
a business glossary in **Dataplex Knowledge Catalog**. It recommends a
glossary structure (categories + terms) and proposes asset-to-term EntryLinks,
grounded in real catalog entries and (optionally) unstructured documents in
Google Cloud Storage. With explicit approval, it can also create, update,
and delete glossary resources end-to-end.

It is built on the [Google ADK](https://adk.dev) and uses the same
Knowledge Catalog primitives as the sibling
[`knowledge_catalog_discovery_agent`](../knowledge_catalog_discovery_agent/).

## What it does

1. **Builds a context graph** from any combination of:
   - Natural-language scope (e.g. *"customer 360 in project foo"*)
   - Explicit filters (project id, system, aspect type)
   - A `gs://bucket/prefix` URI of unstructured docs. Text formats
     (markdown, txt, csv, json, yaml, sql, xml, log) are read directly;
     **PDFs, scanned images, DOCX, PPTX, XLSX, and HTML** are routed
     through **Document AI** when a processor is configured.
2. **Recommends ontology**: glossary identity, 3–10 categories, and 10–40
   terms, each with a definition and cited evidence.
3. **Recommends links**: ranked `synonym` / `related` / `describes`
   EntryLink proposals between terms and catalog entries.
4. **Applies changes on approval**: full CRUD over glossaries, categories,
   terms, and EntryLinks via the Dataplex REST API.

## Architecture

```
knowledge_catalog_business_glossary_agent/
├── agent.py                   # Root ADK agent (orchestrator)
├── config.py                  # Env-var driven configuration
├── utils.py                   # GCS parsing, auth helpers, slugify
├── prompts/
│   ├── root_instructions.md
│   ├── ingestion_agent.md
│   ├── ontology_recommender.md
│   └── link_recommender.md
├── sub_agents/
│   ├── ingestion_agent.py            # Builds the context graph
│   ├── ontology_agent.py             # Recommends categories + terms
│   └── link_agent.py                 # Recommends EntryLinks
└── tools/
    ├── catalog_search.py             # KC SearchEntries + LookupContext
    ├── glossary_crud.py              # Glossary / Category / Term CRUD
    ├── entry_links.py                # EntryLink CRUD
    ├── gcs_ingest.py                 # GCS doc reader (text + DocAI router)
    ├── documentai_ingest.py          # PDF / image / DOCX extraction via DocAI
    ├── context_graph.py              # Concept + co-occurrence graph
    └── ontology.py                   # Deterministic scorers
```

Sub-agents are exposed to the root agent as `AgentTool`s. The root agent
decides which sub-agent to call based on the steward's intent, then
delegates writes to the CRUD tools only after explicit approval.

## Quick Start

### Prerequisites

A Google Cloud project with these APIs enabled:
- Knowledge Catalog (`dataplex.googleapis.com`)
- Vertex AI (`aiplatform.googleapis.com`)
- Cloud Storage (`storage.googleapis.com`)
- Document AI (`documentai.googleapis.com`) — for PDF / scanned-doc ingestion
- Service Usage (`serviceusage.googleapis.com`)

IAM roles (or equivalent permissions) on the consumer project:
- `roles/dataplex.viewer` and `roles/dataplex.editor` (for glossary CRUD)
- `roles/aiplatform.user`
- `roles/storage.objectViewer` on any GCS bucket you ingest from
- `roles/documentai.apiUser` (when DocAI is enabled)
- `roles/serviceusage.serviceUsageConsumer`

### Document AI setup (recommended)

To ingest PDFs, scanned policy docs, and Office files, create one
processor and put its ID in `.env`:

```bash
# One-time: create a Layout Parser processor (best for glossary mining;
# handles PDF, DOCX, PPTX, XLSX, HTML, TXT and preserves block structure).
gcloud documentai processors create \
    --location=us \
    --display-name=glossary-layout-parser \
    --type=LAYOUT_PARSER_PROCESSOR

# Copy the returned processor ID into your .env as DOCUMENT_AI_PROCESSOR_ID.
```

Alternative processor types if Layout Parser isn't a fit:
- `OCR_PROCESSOR` — cheapest, PDFs + scanned images only.
- `FORM_PARSER_PROCESSOR` — best for invoices / structured forms.

The agent **degrades gracefully** if `DOCUMENT_AI_PROCESSOR_ID` is empty:
binary documents are listed but skipped during reading, and the steward
is warned in the response.

### Setup

```bash
git clone https://github.com/GoogleCloudPlatform/dataplex-labs.git
cd dataplex-labs/knowledge_catalog_business_glossary_agent

python3 -m venv /tmp/kcglossary
source /tmp/kcglossary/bin/activate
pip3 install -r requirements.txt

cp .env.example .env
# edit .env with your project id, then:
export $(grep -v '^#' .env | xargs)
```

### Run

```bash
adk run .
```

Or use it as a sub-agent in your own ADK app:

```python
from knowledge_catalog_business_glossary_agent import root_agent as glossary_agent
```

## Usage examples

**NL scope (primary):**
> *"Recommend a glossary for our customer-360 domain in project `acme-data-prod`."*

**Scoped filter (fallback):**
> *"Build a glossary for everything under `system=bigquery projectid=acme-data-prod parent=billing`."*

**GCS unstructured docs:**
> *"Use the docs at `gs://acme-data-docs/glossary-source/` to recommend the supply-chain glossary."*

**Combined NL + GCS:**
> *"Recommend the marketing glossary for `acme-data-prod`, grounded in `gs://acme-data-docs/marketing-wiki/`."*

**Apply changes:**
> *"Looks good — create the glossary and the top 12 terms, but skip 'Cohort'."*

**Add links to an existing glossary:**
> *"Propose links for the existing `customer-360` glossary against the
> billing dataset."*

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_CLOUD_PROJECT` | _(required)_ | Consumer / billing project |
| `GOOGLE_GENAI_USE_VERTEXAI` | `True` | Use Vertex AI for Gemini |
| `DATAPLEX_GLOSSARY_LOCATION` | `global` | Default glossary location |
| `DATAPLEX_API_ENDPOINT` | `dataplex.googleapis.com` | Dataplex endpoint |
| `GLOSSARY_AGENT_MODEL` | `gemini-3-flash-preview` | Gemini model id |
| `GLOSSARY_AGENT_MAX_GCS_DOCS` | `50` | Cap on docs read per turn |
| `GLOSSARY_AGENT_MAX_DOC_BYTES` | `524288` | Per-doc byte cap (text docs) |
| `DOCUMENT_AI_LOCATION` | `us` | Region of the DocAI processor |
| `DOCUMENT_AI_PROCESSOR_ID` | _(empty)_ | DocAI processor UUID; empty disables binary-doc ingestion |
| `DOCUMENT_AI_PROCESSOR_VERSION` | _(empty)_ | Optional pinned processor version |

## Safety model

- Recommendations are produced before any write call.
- Every `create_*` / `update_*` / `delete_*` call requires an explicit
  in-conversation approval from the steward naming the change.
- Tool errors are surfaced verbatim — the agent will stop a destructive
  sequence and ask how to proceed rather than swallow failures.

## References

- [Knowledge Catalog Search API](https://cloud.google.com/dataplex/docs/reference/rest/v1/projects.locations/searchEntries)
- [Business Glossary REST API](https://cloud.google.com/dataplex/docs/reference/rest/v1/projects.locations.glossaries)
- [EntryLinks API](https://cloud.google.com/dataplex/docs/reference/rest/v1/projects.locations.entryGroups.entryLinks)
- [ADK Documentation](https://adk.dev/get-started/python/)
