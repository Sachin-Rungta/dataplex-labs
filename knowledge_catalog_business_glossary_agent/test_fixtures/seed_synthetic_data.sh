#!/usr/bin/env bash
#
# seed_synthetic_data.sh — populate a project with realistic synthetic data
# so the Business Glossary Agent has something meaningful to recommend
# against.
#
# Creates:
#   * BigQuery dataset:  $PROJECT.customer_360
#       - customers, accounts, transactions, support_tickets, products
#       - tables AND columns carry descriptions (Knowledge Catalog indexes them)
#       - ~10–20 rows per table of plausible data
#   * GCS bucket:        gs://$PROJECT-glossary-test
#       - 5 markdown docs describing the business domain
#         (data dictionary, customer lifecycle, subscription model, etc.)
#
# Idempotent: safe to re-run; existing dataset/bucket are reused.
#
# Usage:
#   ./test_fixtures/seed_synthetic_data.sh \
#       --project=sachin-bug-bash-project-1 \
#       [--bq-location=US] \
#       [--bucket-location=US] \
#       [--dataset=customer_360] \
#       [--bucket=<name>]            # default: ${project}-glossary-test
#

set -euo pipefail

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
DATASET="customer_360"
BUCKET=""
BQ_LOCATION="US"
BUCKET_LOCATION="US"

for arg in "$@"; do
  case "$arg" in
    --project=*)         PROJECT="${arg#*=}" ;;
    --dataset=*)         DATASET="${arg#*=}" ;;
    --bucket=*)          BUCKET="${arg#*=}" ;;
    --bq-location=*)     BQ_LOCATION="${arg#*=}" ;;
    --bucket-location=*) BUCKET_LOCATION="${arg#*=}" ;;
    -h|--help)
      awk 'NR==1{next} /^[^#]/{exit} /^# /{sub(/^# /,""); print; next} /^#/{sub(/^#/,""); print}' \
          "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

[[ -z "$PROJECT" ]] && { echo "Set GOOGLE_CLOUD_PROJECT or pass --project=<id>" >&2; exit 1; }
[[ -z "$BUCKET" ]]  && BUCKET="${PROJECT}-glossary-test"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m⚠\033[0m %s\n' "$*"; }
err()   { printf '\033[31m✗\033[0m %s\n' "$*" >&2; }
step()  { echo; bold "▸ $*"; }

command -v bq     >/dev/null || { err "bq not found.";     exit 1; }
command -v gsutil >/dev/null || { err "gsutil not found."; exit 1; }
command -v gcloud >/dev/null || { err "gcloud not found."; exit 1; }

bold "Seeding synthetic data into $PROJECT"
echo "  Dataset : $PROJECT:$DATASET ($BQ_LOCATION)"
echo "  Bucket  : gs://$BUCKET ($BUCKET_LOCATION)"

# ---------------------------------------------------------------------------
# 1. BigQuery dataset + tables
# ---------------------------------------------------------------------------

step "Creating BigQuery dataset $DATASET"
if bq --project_id="$PROJECT" show "$DATASET" >/dev/null 2>&1; then
  ok "Dataset already exists"
else
  bq --project_id="$PROJECT" --location="$BQ_LOCATION" mk \
      --dataset \
      --description="Synthetic customer-360 data for Business Glossary Agent testing." \
      "$PROJECT:$DATASET"
  ok "Created $DATASET"
fi

step "Creating tables with column-level descriptions"

bq --project_id="$PROJECT" --location="$BQ_LOCATION" query --use_legacy_sql=false --quiet <<SQL
CREATE OR REPLACE TABLE \`$PROJECT.$DATASET.customers\` (
  customer_id     STRING  OPTIONS(description="Unique stable identifier for a customer record. Source of truth for all customer-scoped joins."),
  email           STRING  OPTIONS(description="Primary contact email address for the customer."),
  account_status  STRING  OPTIONS(description="Lifecycle state of the customer account: one of 'Active', 'Suspended', 'Churned'. 'Active' means there is a non-cancelled subscription."),
  customer_segment STRING OPTIONS(description="Go-to-market segment the customer belongs to: 'Enterprise', 'SMB', or 'Consumer'. Drives sales motion and pricing tier."),
  lifecycle_stage STRING  OPTIONS(description="Marketing-funnel stage of the customer: 'Lead', 'Prospect', 'Customer', or 'Advocate'."),
  signup_date     DATE    OPTIONS(description="Date the customer first created an account."),
  ltv_usd         FLOAT64 OPTIONS(description="Customer Lifetime Value in USD — modeled total revenue expected over the customer's relationship.")
) OPTIONS(description="Customer master table. One row per customer. The authoritative view of who a customer is and what segment / lifecycle stage they occupy. Joined to accounts and support_tickets.");

CREATE OR REPLACE TABLE \`$PROJECT.$DATASET.accounts\` (
  account_id     STRING    OPTIONS(description="Unique identifier for a subscription account. A customer may own multiple accounts."),
  customer_id    STRING    OPTIONS(description="Foreign key into customers.customer_id. The customer who owns this account."),
  plan_tier      STRING    OPTIONS(description="Subscription plan tier: 'Free', 'Pro', or 'Enterprise'."),
  billing_cycle  STRING    OPTIONS(description="Billing cadence: 'Monthly' or 'Annual'."),
  mrr_usd        FLOAT64   OPTIONS(description="Monthly Recurring Revenue in USD for this account. Annual plans amortized to monthly."),
  started_at     TIMESTAMP OPTIONS(description="Timestamp when the subscription started."),
  canceled_at    TIMESTAMP OPTIONS(description="Timestamp when the subscription was cancelled. NULL for active accounts.")
) OPTIONS(description="Subscription account table. One row per paid or free subscription. The source of MRR, ARR, and churn metrics. Joined to customers and transactions.");

CREATE OR REPLACE TABLE \`$PROJECT.$DATASET.transactions\` (
  transaction_id    STRING    OPTIONS(description="Unique identifier for a billing transaction."),
  account_id        STRING    OPTIONS(description="Foreign key into accounts.account_id."),
  amount_usd        FLOAT64   OPTIONS(description="Transaction amount in USD. Positive for charges, negative for refunds/credits."),
  transaction_type  STRING    OPTIONS(description="Type of transaction: 'Charge', 'Refund', or 'Credit'."),
  payment_method    STRING    OPTIONS(description="Payment instrument used: 'Card', 'ACH', 'Wire', or 'Invoice'."),
  created_at        TIMESTAMP OPTIONS(description="Timestamp when the transaction was recorded.")
) OPTIONS(description="Billing transactions table. One row per charge, refund, or credit. The source of revenue, refunds, and net-billed metrics. Joined to accounts via account_id.");

CREATE OR REPLACE TABLE \`$PROJECT.$DATASET.support_tickets\` (
  ticket_id    STRING    OPTIONS(description="Unique identifier for a support ticket."),
  customer_id  STRING    OPTIONS(description="Foreign key into customers.customer_id. The customer who opened the ticket."),
  priority     STRING    OPTIONS(description="Ticket priority: 'Low', 'Medium', 'High', or 'Critical'."),
  category     STRING    OPTIONS(description="High-level ticket category: 'Billing', 'Technical', or 'Account'."),
  status       STRING    OPTIONS(description="Current ticket state: 'Open', 'In Progress', or 'Resolved'."),
  created_at   TIMESTAMP OPTIONS(description="Timestamp when the ticket was opened by the customer."),
  resolved_at  TIMESTAMP OPTIONS(description="Timestamp when the ticket reached the 'Resolved' state. NULL while still open.")
) OPTIONS(description="Customer support tickets. One row per ticket. The source of support volume, CSAT, and time-to-resolution metrics. Joined to customers via customer_id.");

CREATE OR REPLACE TABLE \`$PROJECT.$DATASET.products\` (
  product_id        STRING  OPTIONS(description="Unique identifier for a product SKU."),
  product_name      STRING  OPTIONS(description="Marketing-facing display name of the product."),
  product_category  STRING  OPTIONS(description="High-level product family: 'Platform', 'Add-on', or 'Service'."),
  sku               STRING  OPTIONS(description="Stock-keeping unit code used by finance and inventory."),
  list_price_usd    FLOAT64 OPTIONS(description="Published list price per month in USD. Actual price may differ via discounts."),
  active            BOOL    OPTIONS(description="TRUE if the product is currently available for purchase.")
) OPTIONS(description="Product catalog. One row per SKU. Static reference data used by accounts and transactions to attribute revenue.");
SQL
ok "Schemas created"

step "Inserting sample rows"

bq --project_id="$PROJECT" --location="$BQ_LOCATION" query --use_legacy_sql=false --quiet <<SQL
INSERT INTO \`$PROJECT.$DATASET.customers\` VALUES
  ('CUST-001', 'alice@northwind.example', 'Active',    'Enterprise', 'Customer', DATE '2023-01-15',  84000.00),
  ('CUST-002', 'bob@globex.example',      'Active',    'SMB',        'Customer', DATE '2023-06-02',  12500.00),
  ('CUST-003', 'carol@initech.example',   'Churned',   'SMB',        'Customer', DATE '2022-11-10',   3200.00),
  ('CUST-004', 'dan@hooli.example',       'Active',    'Enterprise', 'Advocate', DATE '2021-04-20', 196000.00),
  ('CUST-005', 'eve@piedpiper.example',   'Suspended', 'SMB',        'Prospect', DATE '2024-02-12',      0.00),
  ('CUST-006', 'frank@umbrella.example',  'Active',    'Consumer',   'Customer', DATE '2024-09-01',    240.00),
  ('CUST-007', 'grace@stark.example',     'Active',    'Enterprise', 'Customer', DATE '2023-08-19', 110000.00),
  ('CUST-008', 'heidi@wonka.example',     'Active',    'Consumer',   'Customer', DATE '2025-01-05',    480.00),
  ('CUST-009', 'ivan@vandelay.example',   'Churned',   'Enterprise', 'Customer', DATE '2022-03-08',  64000.00),
  ('CUST-010', 'judy@cyberdyne.example',  'Active',    'SMB',        'Advocate', DATE '2023-12-21',  28000.00);

INSERT INTO \`$PROJECT.$DATASET.accounts\` VALUES
  ('ACC-1001', 'CUST-001', 'Enterprise', 'Annual',  6000.00, TIMESTAMP '2023-01-20 09:00:00', NULL),
  ('ACC-1002', 'CUST-002', 'Pro',        'Monthly',  299.00, TIMESTAMP '2023-06-04 12:00:00', NULL),
  ('ACC-1003', 'CUST-003', 'Pro',        'Monthly',  299.00, TIMESTAMP '2022-11-12 10:00:00', TIMESTAMP '2024-05-30 17:30:00'),
  ('ACC-1004', 'CUST-004', 'Enterprise', 'Annual',  9800.00, TIMESTAMP '2021-04-25 11:00:00', NULL),
  ('ACC-1005', 'CUST-005', 'Pro',        'Monthly',  299.00, TIMESTAMP '2024-02-12 14:00:00', NULL),
  ('ACC-1006', 'CUST-006', 'Free',       'Monthly',    0.00, TIMESTAMP '2024-09-01 08:00:00', NULL),
  ('ACC-1007', 'CUST-007', 'Enterprise', 'Annual',  7500.00, TIMESTAMP '2023-08-21 09:30:00', NULL),
  ('ACC-1008', 'CUST-008', 'Pro',        'Annual',   249.00, TIMESTAMP '2025-01-05 10:00:00', NULL),
  ('ACC-1009', 'CUST-009', 'Enterprise', 'Annual',  5400.00, TIMESTAMP '2022-03-10 15:00:00', TIMESTAMP '2024-09-15 11:00:00'),
  ('ACC-1010', 'CUST-010', 'Pro',        'Monthly',  299.00, TIMESTAMP '2023-12-22 09:00:00', NULL);

INSERT INTO \`$PROJECT.$DATASET.transactions\` VALUES
  ('TXN-200001', 'ACC-1001', 72000.00, 'Charge', 'Wire',    TIMESTAMP '2025-01-20 09:05:00'),
  ('TXN-200002', 'ACC-1002',   299.00, 'Charge', 'Card',    TIMESTAMP '2025-05-04 12:00:00'),
  ('TXN-200003', 'ACC-1003',   299.00, 'Charge', 'Card',    TIMESTAMP '2024-04-12 10:01:00'),
  ('TXN-200004', 'ACC-1003',  -150.00, 'Refund', 'Card',    TIMESTAMP '2024-05-31 09:15:00'),
  ('TXN-200005', 'ACC-1004',117600.00, 'Charge', 'Invoice', TIMESTAMP '2025-04-25 11:30:00'),
  ('TXN-200006', 'ACC-1007', 90000.00, 'Charge', 'Wire',    TIMESTAMP '2025-08-21 09:32:00'),
  ('TXN-200007', 'ACC-1008',  2988.00, 'Charge', 'Card',    TIMESTAMP '2025-01-05 10:05:00'),
  ('TXN-200008', 'ACC-1010',   299.00, 'Charge', 'ACH',     TIMESTAMP '2025-05-22 09:00:00'),
  ('TXN-200009', 'ACC-1010',  -299.00, 'Credit', 'ACH',     TIMESTAMP '2025-05-25 14:00:00'),
  ('TXN-200010', 'ACC-1002',   299.00, 'Charge', 'Card',    TIMESTAMP '2025-06-04 12:00:00');

INSERT INTO \`$PROJECT.$DATASET.support_tickets\` VALUES
  ('TKT-30001', 'CUST-001', 'High',     'Technical', 'Resolved',    TIMESTAMP '2025-03-12 09:00:00', TIMESTAMP '2025-03-12 17:30:00'),
  ('TKT-30002', 'CUST-002', 'Low',      'Billing',   'Resolved',    TIMESTAMP '2025-04-02 10:15:00', TIMESTAMP '2025-04-03 09:00:00'),
  ('TKT-30003', 'CUST-003', 'Medium',   'Account',   'Resolved',    TIMESTAMP '2024-05-28 14:00:00', TIMESTAMP '2024-05-30 17:30:00'),
  ('TKT-30004', 'CUST-004', 'Critical', 'Technical', 'In Progress', TIMESTAMP '2025-06-10 08:00:00', NULL),
  ('TKT-30005', 'CUST-007', 'High',     'Billing',   'Open',        TIMESTAMP '2025-06-15 11:20:00', NULL),
  ('TKT-30006', 'CUST-006', 'Low',      'Account',   'Resolved',    TIMESTAMP '2025-02-19 13:00:00', TIMESTAMP '2025-02-19 14:00:00'),
  ('TKT-30007', 'CUST-010', 'Medium',   'Technical', 'Resolved',    TIMESTAMP '2025-05-22 09:30:00', TIMESTAMP '2025-05-23 12:00:00'),
  ('TKT-30008', 'CUST-008', 'High',     'Billing',   'Open',        TIMESTAMP '2025-06-18 07:50:00', NULL);

INSERT INTO \`$PROJECT.$DATASET.products\` VALUES
  ('PRD-001', 'Platform Pro',         'Platform', 'SKU-PLT-PRO',  299.00,  TRUE),
  ('PRD-002', 'Platform Enterprise',  'Platform', 'SKU-PLT-ENT', 1999.00,  TRUE),
  ('PRD-003', 'Platform Free',        'Platform', 'SKU-PLT-FRE',    0.00,  TRUE),
  ('PRD-004', 'Analytics Add-on',     'Add-on',   'SKU-ADD-ANL',  149.00,  TRUE),
  ('PRD-005', 'SSO Add-on',           'Add-on',   'SKU-ADD-SSO',  199.00,  TRUE),
  ('PRD-006', 'Premium Support',      'Service',  'SKU-SVC-SUP', 2500.00,  TRUE),
  ('PRD-007', 'Onboarding (Legacy)',  'Service',  'SKU-SVC-OBL',  500.00, FALSE);
SQL
ok "Rows inserted"

# ---------------------------------------------------------------------------
# 2. GCS bucket + business markdown docs
# ---------------------------------------------------------------------------

step "Creating GCS bucket gs://$BUCKET"
if gsutil ls -b "gs://$BUCKET" >/dev/null 2>&1; then
  ok "Bucket already exists"
else
  gsutil mb -p "$PROJECT" -l "$BUCKET_LOCATION" "gs://$BUCKET"
  ok "Created gs://$BUCKET"
fi

step "Uploading domain markdown docs"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Doc 1 — partial business glossary draft
cat >"$TMP/01-customer-glossary-draft.md" <<'MD'
# Customer Glossary (working draft)

This is the working draft of our customer-domain glossary, maintained by
the Data Steward team. It is intentionally partial — the data team will
turn it into the canonical glossary in Dataplex Knowledge Catalog.

## Customer
A natural or legal person who has, at any point, created an account with
us. A Customer is identified by a stable `customer_id`. Customers may
own zero or more Accounts.

## Active Customer
A Customer whose `account_status = 'Active'`, i.e. they have at least
one non-cancelled Account. Used as the denominator for engagement and
retention metrics.

## Customer Segment
The go-to-market bucket a Customer belongs to. Drives pricing tier and
sales motion. Allowed values: `Enterprise`, `SMB`, `Consumer`.

## Lifecycle Stage
Marketing-funnel position of a Customer. Allowed values: `Lead`,
`Prospect`, `Customer`, `Advocate`. Independent of `account_status`.

## Customer Lifetime Value (CLV / LTV)
Modeled total revenue expected from a Customer over the entire
relationship, in USD. Reported on the Customer record. Updated nightly.

## Churn
A Customer transitions to "churned" when all of their Accounts have a
non-null `canceled_at`. A Churned Customer has zero active subscriptions.

## Advocate
A Customer who has opted into referral / case-study programs. Strongest
Lifecycle Stage. Driven by NPS and engagement, not directly editable in
the customer record.
MD

# Doc 2 — data dictionary
cat >"$TMP/02-data-dictionary.md" <<'MD'
# Customer 360 — Data Dictionary

## Tables

| Table | What it represents |
| --- | --- |
| `customers` | Master table of customers. One row per `customer_id`. |
| `accounts` | Paid or free subscription accounts. One row per `account_id`. |
| `transactions` | Billing events (charges, refunds, credits). |
| `support_tickets` | Support cases opened by customers. |
| `products` | Static product catalog / SKU reference. |

## Important columns

### customers.account_status
- `Active`: at least one open Account.
- `Suspended`: account exists but payment failed.
- `Churned`: all Accounts have a non-null `canceled_at`.

### accounts.plan_tier
Plan tiers map to product SKUs:
- `Free` → SKU-PLT-FRE
- `Pro` → SKU-PLT-PRO
- `Enterprise` → SKU-PLT-ENT

### accounts.mrr_usd
Monthly Recurring Revenue in USD. Annual plans are amortized: a $9,800
annual contract surfaces here as $816.67 monthly. Sum of mrr_usd across
active accounts == company MRR.

### transactions.transaction_type
- `Charge`: revenue event, positive `amount_usd`.
- `Refund`: reverses a Charge, negative `amount_usd`.
- `Credit`: customer credit applied, negative `amount_usd`. Not a refund.

### support_tickets.priority
Drives SLA targets:
- `Low`: 5 business days
- `Medium`: 2 business days
- `High`: 8 business hours
- `Critical`: 1 hour, on-call paged
MD

# Doc 3 — customer lifecycle
cat >"$TMP/03-customer-lifecycle.md" <<'MD'
# Customer Lifecycle

Customers move through five Lifecycle Stages:

1. **Lead** — Identified by marketing; has not yet signed up.
2. **Prospect** — Has signed up but never converted to a paid plan.
3. **Customer** — Has at least one paid Account.
4. **Advocate** — A Customer who has referred at least one other
   Customer, or contributed a case study.

The Lifecycle Stage is separate from `account_status`. A Customer can
be in stage `Customer` and have status `Suspended` (payment problem),
or be in stage `Advocate` and have status `Churned` (referrals don't
expire).

## Subscription model

We sell three subscription Plan Tiers:
- **Free**: $0/mo, no SLA.
- **Pro**: $299/mo or $249/mo if annual. Includes email support.
- **Enterprise**: starts at $6,000/year, includes Premium Support and
  the SSO Add-on.

Add-ons are billed alongside the subscription. They include:
- Analytics Add-on ($149/mo)
- SSO Add-on ($199/mo, free with Enterprise)

## Net Revenue

Net Revenue for a period = SUM(amount_usd) for transactions in that
period. Refunds and Credits are negative and reduce Net Revenue. Do not
confuse with MRR (forward-looking) or ARR (12 × MRR).
MD

# Doc 4 — support policy
cat >"$TMP/04-support-policy.md" <<'MD'
# Support Policy

## Ticket Category

Tickets are categorized into three buckets:

- **Billing** — refunds, invoice questions, payment method updates.
  Routed to Finance Ops.
- **Technical** — product bugs, integration issues, API errors. Routed
  to Engineering.
- **Account** — login problems, role / permission changes, identity.
  Routed to CSM.

## Ticket Status

A ticket flows through:
1. `Open` — created by customer, not yet acknowledged.
2. `In Progress` — assigned to an agent, work underway.
3. `Resolved` — agent marked closed; customer has 7 days to reopen.

## Time to Resolution

`resolved_at - created_at`, computed only for tickets with status
`Resolved`. Mean TTR by priority is the headline metric.

## Customer Satisfaction (CSAT)
Survey sent on resolution. Not yet stored in this dataset.
MD

# Doc 5 — KPI definitions
cat >"$TMP/05-kpi-definitions.md" <<'MD'
# Financial & Customer KPI Definitions

## MRR (Monthly Recurring Revenue)
Sum of `mrr_usd` across all Accounts whose `canceled_at IS NULL`.
Updated nightly. Drives the company growth chart.

## ARR (Annual Recurring Revenue)
12 × MRR. Reported in board decks.

## Gross Churn Rate
Cancelled Accounts in period / Active Accounts at start of period.

## Net Revenue Retention (NRR)
(Starting MRR + Expansion - Churn - Contraction) / Starting MRR.

## CLV / LTV
See `01-customer-glossary-draft.md` § Customer Lifetime Value.

## Average Revenue Per Account (ARPA)
MRR / count(Active Accounts).

## Support TTR (Time to Resolution)
See `04-support-policy.md` § Time to Resolution.

## Active Customer Count
Distinct `customer_id` where `account_status = 'Active'`.
MD

gsutil -m cp "$TMP"/*.md "gs://$BUCKET/" >/dev/null
ok "Uploaded 5 markdown docs to gs://$BUCKET/"

# ---------------------------------------------------------------------------
# Binary doc — generates a single PDF from the glossary draft so Document
# AI Layout Parser actually gets exercised end-to-end. The agent's GCS
# reader will route the .pdf through DocAI when DOCUMENT_AI_PROCESSOR_ID
# is set; without DocAI, the file will appear in the doc list with
# status: skipped (which is the steward's signal that DocAI is off).
# ---------------------------------------------------------------------------

step "Generating PDF for DocAI exercise"
MAKE_PDF_HELPER="$(dirname "${BASH_SOURCE[0]}")/lib/make_pdf.py"
if [[ ! -f "$MAKE_PDF_HELPER" ]]; then
  warn "Helper not found at $MAKE_PDF_HELPER; skipping PDF generation."
else
  PDF_OUT="$TMP/06-customer-glossary-draft.pdf"
  if python3 "$MAKE_PDF_HELPER" \
      "Customer Glossary - Working Draft" \
      "$TMP/01-customer-glossary-draft.md" >"$PDF_OUT" 2>/dev/null; then
    gsutil cp "$PDF_OUT" "gs://$BUCKET/" >/dev/null
    ok "Uploaded gs://$BUCKET/06-customer-glossary-draft.pdf"
  else
    warn "PDF generation failed; markdown-only upload completed."
  fi
fi

# ---------------------------------------------------------------------------
# 3. Summary
# ---------------------------------------------------------------------------

step "Done"
cat <<EOF
BigQuery dataset: $PROJECT:$DATASET
Tables:
  - customers (10 rows)
  - accounts (10 rows)
  - transactions (10 rows)
  - support_tickets (8 rows)
  - products (7 rows)

GCS bucket: gs://$BUCKET
Docs:
  - 01-customer-glossary-draft.md
  - 02-data-dictionary.md
  - 03-customer-lifecycle.md
  - 04-support-policy.md
  - 05-kpi-definitions.md
  - 06-customer-glossary-draft.pdf   (routed through Document AI Layout
                                      Parser when DOCUMENT_AI_PROCESSOR_ID
                                      is set; otherwise listed as
                                      'status: skipped' in the eval report)

Knowledge Catalog typically indexes new BigQuery tables within 5–15
minutes. After that, run:

  adk run .

and ask:

  Recommend a new glossary for our customer-360 domain in project
  $PROJECT, grounded in gs://$BUCKET/. Focus on business concepts.

EOF
