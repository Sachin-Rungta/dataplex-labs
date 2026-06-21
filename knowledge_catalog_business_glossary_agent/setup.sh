#!/usr/bin/env bash
#
# setup.sh — one-shot bootstrap for the Knowledge Catalog Business Glossary Agent.
#
# Enables required APIs, grants IAM roles to the running identity (or an
# explicitly provided principal), optionally creates a Document AI Layout
# Parser processor, writes .env with concrete values, and installs the
# Python dependencies into a virtualenv. Every step is idempotent and can
# be skipped independently with --skip-* flags.
#
# Usage:
#   ./setup.sh \
#       --project=my-gcp-project \
#       --vertex-location=us-central1 \
#       [--principal=user:foo@example.com] \
#       [--gcs-bucket=my-docs-bucket] \
#       [--enable-lineage] \
#       [--skip-docai] [--skip-iam] [--skip-apis] \
#       [--skip-venv] [--skip-install]
#
# Common short forms:
#   ./setup.sh --project=$GOOGLE_CLOUD_PROJECT                 # all defaults
#   ./setup.sh --project=foo --enable-lineage                  # turn lineage on
#   ./setup.sh --project=foo --skip-docai --skip-venv          # minimal
#

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
PRINCIPAL=""
VERTEX_LOCATION="us-central1"
DOCAI_LOCATION="us"
GLOSSARY_LOCATION="global"
LINEAGE_LOCATION="us"
GCS_BUCKET=""
DOCAI_DISPLAY_NAME="glossary-layout-parser"
ENABLE_LINEAGE=false

SKIP_APIS=false
SKIP_IAM=false
SKIP_DOCAI=false
SKIP_VENV=false
SKIP_INSTALL=false
SKIP_ENV=false

VENV_PATH=".venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

usage() {
  # Print the header doc block (lines starting with "#" up to the first blank line).
  awk '
    NR==1 {next}                      # skip shebang
    /^[^#]/ {exit}                    # stop at the first non-comment line
    /^#$/ {print ""; next}            # blank comment → blank line
    /^# / {sub(/^# /, ""); print; next}
    /^#/  {sub(/^#/, ""); print}
  ' "${BASH_SOURCE[0]}"
  exit "${1:-0}"
}

for arg in "$@"; do
  case "$arg" in
    --project=*)          PROJECT="${arg#*=}" ;;
    --principal=*)        PRINCIPAL="${arg#*=}" ;;
    --vertex-location=*)  VERTEX_LOCATION="${arg#*=}" ;;
    --docai-location=*)   DOCAI_LOCATION="${arg#*=}" ;;
    --glossary-location=*) GLOSSARY_LOCATION="${arg#*=}" ;;
    --lineage-location=*) LINEAGE_LOCATION="${arg#*=}" ;;
    --gcs-bucket=*)       GCS_BUCKET="${arg#*=}" ;;
    --docai-name=*)       DOCAI_DISPLAY_NAME="${arg#*=}" ;;
    --venv-path=*)        VENV_PATH="${arg#*=}" ;;
    --enable-lineage)     ENABLE_LINEAGE=true ;;
    --skip-apis)          SKIP_APIS=true ;;
    --skip-iam)           SKIP_IAM=true ;;
    --skip-docai)         SKIP_DOCAI=true ;;
    --skip-venv)          SKIP_VENV=true ;;
    --skip-install)       SKIP_INSTALL=true ;;
    --skip-env)           SKIP_ENV=true ;;
    -h|--help)            usage 0 ;;
    *) echo "Unknown arg: $arg" >&2; usage 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
dim()   { printf '\033[2m%s\033[0m\n' "$*"; }
ok()    { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m⚠\033[0m %s\n' "$*"; }
err()   { printf '\033[31m✗\033[0m %s\n' "$*" >&2; }

step() {
  echo
  bold "▸ $*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "$1 is required but not found on PATH."
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

bold "Knowledge Catalog Business Glossary Agent — setup"
echo "Script dir: $SCRIPT_DIR"

step "Checking prerequisites"
require_cmd gcloud
require_cmd python3
ok "gcloud:  $(gcloud --version | head -1)"
ok "python3: $(python3 --version)"

if [[ -z "$PROJECT" ]]; then
  err "No project. Set GOOGLE_CLOUD_PROJECT or pass --project=<id>."
  exit 1
fi
ok "Project: $PROJECT"

# Resolve principal if not supplied.
if [[ -z "$PRINCIPAL" ]]; then
  ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1 || true)"
  if [[ -z "$ACTIVE_ACCOUNT" ]]; then
    err "No active gcloud account. Run 'gcloud auth login' first."
    exit 1
  fi
  if [[ "$ACTIVE_ACCOUNT" == *".gserviceaccount.com" ]]; then
    PRINCIPAL="serviceAccount:$ACTIVE_ACCOUNT"
  else
    PRINCIPAL="user:$ACTIVE_ACCOUNT"
  fi
  ok "Principal (detected): $PRINCIPAL"
else
  ok "Principal: $PRINCIPAL"
fi

# Set the project as the active gcloud project for any later calls.
gcloud config set project "$PROJECT" >/dev/null 2>&1
ok "gcloud project set to $PROJECT"

# ---------------------------------------------------------------------------
# 1. Enable APIs
# ---------------------------------------------------------------------------

APIS=(
  dataplex.googleapis.com
  aiplatform.googleapis.com
  storage.googleapis.com
  serviceusage.googleapis.com
  documentai.googleapis.com
)
if "$ENABLE_LINEAGE"; then
  APIS+=( datalineage.googleapis.com )
fi

if "$SKIP_APIS"; then
  step "Skipping API enablement (--skip-apis)"
else
  step "Enabling APIs (${#APIS[@]})"
  if gcloud services enable "${APIS[@]}" --project="$PROJECT"; then
    for api in "${APIS[@]}"; do ok "$api"; done
  else
    err "Failed to enable one or more APIs. Check IAM (need roles/serviceusage.serviceUsageAdmin)."
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# 2. IAM
# ---------------------------------------------------------------------------

ROLES=(
  roles/dataplex.editor
  roles/dataplex.viewer
  roles/aiplatform.user
  roles/documentai.apiUser
  roles/serviceusage.serviceUsageConsumer
)
if "$ENABLE_LINEAGE"; then
  ROLES+=( roles/datalineage.viewer )
fi

if "$SKIP_IAM"; then
  step "Skipping IAM bindings (--skip-iam)"
else
  step "Granting IAM roles to $PRINCIPAL"
  for role in "${ROLES[@]}"; do
    if gcloud projects add-iam-policy-binding "$PROJECT" \
        --member="$PRINCIPAL" \
        --role="$role" \
        --condition=None \
        --quiet >/dev/null 2>&1; then
      ok "$role"
    else
      err "Failed to grant $role. Continuing — check policy manually."
    fi
  done

  if [[ -n "$GCS_BUCKET" ]]; then
    step "Granting roles/storage.objectViewer on gs://$GCS_BUCKET"
    if command -v gsutil >/dev/null 2>&1; then
      if gsutil iam ch "${PRINCIPAL}:objectViewer" "gs://$GCS_BUCKET" >/dev/null 2>&1; then
        ok "gs://$GCS_BUCKET (objectViewer)"
      else
        warn "Couldn't grant objectViewer on gs://$GCS_BUCKET. Granting project-wide instead."
        gcloud projects add-iam-policy-binding "$PROJECT" \
            --member="$PRINCIPAL" \
            --role="roles/storage.objectViewer" \
            --condition=None --quiet >/dev/null 2>&1 || true
        ok "roles/storage.objectViewer (project-wide fallback)"
      fi
    else
      warn "gsutil not found; falling back to project-wide roles/storage.objectViewer."
      gcloud projects add-iam-policy-binding "$PROJECT" \
          --member="$PRINCIPAL" \
          --role="roles/storage.objectViewer" \
          --condition=None --quiet >/dev/null 2>&1 || true
      ok "roles/storage.objectViewer"
    fi
  else
    step "Granting roles/storage.objectViewer (project-wide)"
    if gcloud projects add-iam-policy-binding "$PROJECT" \
        --member="$PRINCIPAL" \
        --role="roles/storage.objectViewer" \
        --condition=None --quiet >/dev/null 2>&1; then
      ok "roles/storage.objectViewer"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 3. Document AI processor
# ---------------------------------------------------------------------------

DOCAI_PROCESSOR_ID=""
if "$SKIP_DOCAI"; then
  step "Skipping Document AI processor (--skip-docai)"
else
  step "Document AI Layout Parser processor"
  # Idempotency: look up an existing processor with the same display name.
  EXISTING="$(gcloud documentai processors list \
      --location="$DOCAI_LOCATION" \
      --project="$PROJECT" \
      --filter="displayName=$DOCAI_DISPLAY_NAME" \
      --format='value(name)' 2>/dev/null | head -1 || true)"

  if [[ -n "$EXISTING" ]]; then
    DOCAI_PROCESSOR_ID="${EXISTING##*/}"
    ok "Found existing processor: $EXISTING"
  else
    CREATE_OUTPUT="$(gcloud documentai processors create \
        --location="$DOCAI_LOCATION" \
        --project="$PROJECT" \
        --display-name="$DOCAI_DISPLAY_NAME" \
        --type=LAYOUT_PARSER_PROCESSOR \
        --format='value(name)' 2>&1 || true)"
    if [[ "$CREATE_OUTPUT" == projects/* ]]; then
      DOCAI_PROCESSOR_ID="${CREATE_OUTPUT##*/}"
      ok "Created processor: $CREATE_OUTPUT"
    else
      warn "Could not create DocAI processor. Output: $CREATE_OUTPUT"
      warn "Continuing without DocAI. Re-run with --skip-docai or create manually."
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 4. .env
# ---------------------------------------------------------------------------

if "$SKIP_ENV"; then
  step "Skipping .env generation (--skip-env)"
else
  step "Writing $ENV_FILE"
  if [[ -f "$ENV_FILE" ]]; then
    BACKUP="$ENV_FILE.bak.$(date +%s)"
    cp "$ENV_FILE" "$BACKUP"
    warn "Existing .env backed up to $BACKUP"
  fi

  cat >"$ENV_FILE" <<EOF
# Generated by setup.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ---- Required ----
GOOGLE_CLOUD_PROJECT=$PROJECT
GOOGLE_GENAI_USE_VERTEXAI=True
VERTEX_LOCATION=$VERTEX_LOCATION

# ---- Dataplex ----
DATAPLEX_GLOSSARY_LOCATION=$GLOSSARY_LOCATION
DATAPLEX_API_ENDPOINT=dataplex.googleapis.com

# ---- Models ----
GLOSSARY_AGENT_MODEL=gemini-2.5-flash
GLOSSARY_AGENT_CLASSIFIER_MODEL=gemini-2.5-flash
GLOSSARY_AGENT_EMBEDDING_MODEL=text-embedding-005
GLOSSARY_AGENT_EMBEDDING_DIM=0
GLOSSARY_AGENT_EMBEDDING_BATCH=100

# ---- Ingestion ----
GLOSSARY_AGENT_MAX_GCS_DOCS=50
GLOSSARY_AGENT_MAX_DOC_BYTES=524288

# ---- Document AI ----
DOCUMENT_AI_LOCATION=$DOCAI_LOCATION
DOCUMENT_AI_PROCESSOR_ID=$DOCAI_PROCESSOR_ID
DOCUMENT_AI_PROCESSOR_VERSION=

# ---- Recommendation thresholds ----
GLOSSARY_AGENT_LINK_COSINE_MIN=0.45
GLOSSARY_AGENT_LINK_COSINE_STRONG=0.72
GLOSSARY_AGENT_DEDUP_COSINE=0.78
GLOSSARY_AGENT_CLUSTER_DISTANCE=0.55
GLOSSARY_AGENT_MIN_CLUSTER_SIZE=3
GLOSSARY_AGENT_MAX_CATEGORIES=10
GLOSSARY_AGENT_MAX_TERMS=40
GLOSSARY_AGENT_MAX_CLASSIFIER_PAIRS=200

# ---- Data Lineage (opt-in) ----
GLOSSARY_AGENT_USE_LINEAGE=$ENABLE_LINEAGE
LINEAGE_LOCATION=$LINEAGE_LOCATION
LINEAGE_MAX_HOPS=1
LINEAGE_MAX_NEIGHBORS=25
EOF
  ok "Wrote $ENV_FILE"
fi

# ---------------------------------------------------------------------------
# 5. Virtualenv + dependencies
# ---------------------------------------------------------------------------

VENV_ABS="$SCRIPT_DIR/$VENV_PATH"
if [[ "$VENV_PATH" = /* ]]; then VENV_ABS="$VENV_PATH"; fi

if "$SKIP_VENV"; then
  step "Skipping virtualenv (--skip-venv)"
else
  step "Creating virtualenv at $VENV_ABS"
  if [[ -d "$VENV_ABS" ]]; then
    ok "Virtualenv already exists"
  else
    python3 -m venv "$VENV_ABS"
    ok "Created $VENV_ABS"
  fi
fi

if "$SKIP_INSTALL"; then
  step "Skipping pip install (--skip-install)"
else
  step "Installing requirements"
  PIP_BIN="pip3"
  if [[ -d "$VENV_ABS" ]]; then
    # shellcheck disable=SC1091
    PIP_BIN="$VENV_ABS/bin/pip"
  fi
  # Many corp Python environments are pre-configured to use an internal
  # Artifact Registry mirror (e.g. ah-3p-staging-python) as the primary
  # index, which does not carry packages like google-adk. Adding
  # public PyPI as an --extra-index-url makes pip fall back to it for
  # anything the corp mirror lacks, while keeping any corp-required
  # packages served from the mirror.
  PYPI_FALLBACK="${PIP_EXTRA_INDEX_URL:-https://pypi.org/simple/}"
  "$PIP_BIN" install --upgrade pip --extra-index-url "$PYPI_FALLBACK" >/dev/null
  "$PIP_BIN" install -r "$SCRIPT_DIR/requirements.txt" \
      --extra-index-url "$PYPI_FALLBACK"
  ok "Dependencies installed (extra-index-url: $PYPI_FALLBACK)."
fi

# ---------------------------------------------------------------------------
# 6. ADC reminder + next steps
# ---------------------------------------------------------------------------

step "Application Default Credentials (ADC) check"
if gcloud auth application-default print-access-token >/dev/null 2>&1; then
  ok "ADC already configured."
else
  warn "ADC not configured. Run this once:"
  echo "    gcloud auth application-default login"
fi

step "Next steps"
echo "  1. Activate the venv:"
echo "       source $VENV_ABS/bin/activate"
echo "  2. Load the env:"
echo "       export \$(grep -v '^#' $ENV_FILE | xargs)"
echo "  3. Run the agent:"
echo "       adk run ."
echo
ok "Setup complete."
