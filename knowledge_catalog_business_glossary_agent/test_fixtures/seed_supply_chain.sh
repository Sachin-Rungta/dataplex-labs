#!/usr/bin/env bash
#
# seed_supply_chain.sh — second synthetic domain (procurement + logistics)
# so the agent has a non-customer-360 domain to demonstrate on and so the
# eval harness can score it on more than one vocabulary.
#
# Creates:
#   * BigQuery dataset:  $PROJECT.supply_chain
#       - suppliers, purchase_orders, shipments, warehouses, inventory_items
#   * GCS bucket prefix: gs://$PROJECT-glossary-test/supply-chain/
#       - 5 markdown docs (procurement glossary draft, SLA policy, etc.)
#
# Idempotent.
#
# Usage:
#   ./test_fixtures/seed_supply_chain.sh \
#       --project=sachin-bug-bash-project-1 \
#       [--bq-location=US] \
#       [--bucket=<name>]            # default: ${project}-glossary-test
#

set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
DATASET="supply_chain"
BUCKET=""
BQ_LOCATION="US"

for arg in "$@"; do
  case "$arg" in
    --project=*)         PROJECT="${arg#*=}" ;;
    --dataset=*)         DATASET="${arg#*=}" ;;
    --bucket=*)          BUCKET="${arg#*=}" ;;
    --bq-location=*)     BQ_LOCATION="${arg#*=}" ;;
    -h|--help)
      awk 'NR==1{next} /^[^#]/{exit} /^# /{sub(/^# /,""); print; next} /^#/{sub(/^#/,""); print}' \
          "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

[[ -z "$PROJECT" ]] && { echo "Set GOOGLE_CLOUD_PROJECT or --project=<id>" >&2; exit 1; }
[[ -z "$BUCKET" ]]  && BUCKET="${PROJECT}-glossary-test"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '\033[32m✓\033[0m %s\n' "$*"; }
step()  { echo; bold "▸ $*"; }

bold "Seeding supply_chain into $PROJECT"
echo "  Dataset : $PROJECT:$DATASET ($BQ_LOCATION)"
echo "  Bucket  : gs://$BUCKET/supply-chain/"

step "Creating dataset"
if bq --project_id="$PROJECT" show "$DATASET" >/dev/null 2>&1; then
  ok "Dataset already exists"
else
  bq --project_id="$PROJECT" --location="$BQ_LOCATION" mk --dataset \
      --description="Synthetic supply-chain / procurement data for Business Glossary Agent testing." \
      "$PROJECT:$DATASET"
  ok "Created $DATASET"
fi

step "Creating tables"
bq --project_id="$PROJECT" --location="$BQ_LOCATION" query --use_legacy_sql=false --quiet <<SQL
CREATE OR REPLACE TABLE \`$PROJECT.$DATASET.suppliers\` (
  supplier_id     STRING  OPTIONS(description="Unique identifier for a supplier (vendor)."),
  supplier_name   STRING  OPTIONS(description="Legal name of the supplier company."),
  supplier_tier   STRING  OPTIONS(description="Supplier strategic tier: 'Strategic', 'Preferred', or 'Approved'. Drives sourcing priority."),
  country         STRING  OPTIONS(description="ISO-3166 country code where the supplier is headquartered."),
  payment_terms   STRING  OPTIONS(description="Negotiated payment terms, e.g. 'Net 30', 'Net 60'."),
  onboarded_date  DATE    OPTIONS(description="Date the supplier completed onboarding and was approved for purchase orders.")
) OPTIONS(description="Supplier master. One row per approved vendor. Source of truth for sourcing decisions, contracts, and supplier scorecards.");

CREATE OR REPLACE TABLE \`$PROJECT.$DATASET.purchase_orders\` (
  po_id              STRING    OPTIONS(description="Purchase Order identifier (also called PO number)."),
  supplier_id        STRING    OPTIONS(description="Foreign key into suppliers.supplier_id."),
  po_status          STRING    OPTIONS(description="Lifecycle status: 'Draft', 'Issued', 'Acknowledged', 'Fulfilled', or 'Cancelled'."),
  total_amount_usd   FLOAT64   OPTIONS(description="Total committed spend for this PO in USD."),
  currency           STRING    OPTIONS(description="ISO-4217 currency code the supplier is invoiced in. Total is also recorded in USD."),
  requested_date     DATE      OPTIONS(description="Date the requester needed the goods/services delivered by."),
  issued_at          TIMESTAMP OPTIONS(description="Timestamp the PO was sent to the supplier."),
  cost_center        STRING    OPTIONS(description="Internal cost center charged for the PO.")
) OPTIONS(description="Purchase Orders. One row per PO. Source of committed spend, supplier scorecards, and on-time delivery metrics.");

CREATE OR REPLACE TABLE \`$PROJECT.$DATASET.shipments\` (
  shipment_id       STRING    OPTIONS(description="Unique identifier for an inbound shipment."),
  po_id             STRING    OPTIONS(description="Foreign key into purchase_orders.po_id. A PO may produce multiple shipments (partial fulfilment)."),
  carrier           STRING    OPTIONS(description="Carrier name responsible for the lane (e.g. 'FedEx', 'DHL', 'Maersk')."),
  shipment_status   STRING    OPTIONS(description="Status: 'In Transit', 'Delivered', 'Delayed', 'Lost'. Drives on-time and exception metrics."),
  origin_country    STRING    OPTIONS(description="ISO-3166 country code where the shipment originated."),
  destination_warehouse_id STRING OPTIONS(description="Foreign key into warehouses.warehouse_id. The receiving warehouse."),
  estimated_arrival TIMESTAMP OPTIONS(description="Carrier's estimated delivery timestamp at origin time."),
  actual_arrival    TIMESTAMP OPTIONS(description="Actual timestamp the shipment arrived at the warehouse. NULL if not yet delivered.")
) OPTIONS(description="Inbound shipments. One row per shipment leg. Source of on-time delivery rate, dwell time, and inbound exception metrics.");

CREATE OR REPLACE TABLE \`$PROJECT.$DATASET.warehouses\` (
  warehouse_id      STRING  OPTIONS(description="Unique identifier for a warehouse / distribution center."),
  warehouse_name    STRING  OPTIONS(description="Display name of the warehouse."),
  region            STRING  OPTIONS(description="Operational region: 'NA', 'EMEA', 'APAC', 'LATAM'."),
  capacity_pallets  INT64   OPTIONS(description="Maximum pallet capacity (count)."),
  active            BOOL    OPTIONS(description="TRUE if the warehouse is currently operational.")
) OPTIONS(description="Warehouse master. One row per physical receiving / fulfilment location.");

CREATE OR REPLACE TABLE \`$PROJECT.$DATASET.inventory_items\` (
  sku               STRING  OPTIONS(description="Stock-keeping unit code. Stable identifier for an item across warehouses."),
  description       STRING  OPTIONS(description="Human-readable item description."),
  warehouse_id      STRING  OPTIONS(description="Foreign key into warehouses.warehouse_id. Where this stock is held."),
  quantity_on_hand  INT64   OPTIONS(description="Current physical stock count at the warehouse. Decreases on outbound fulfilment, increases on shipment receipt."),
  reorder_point     INT64   OPTIONS(description="Quantity threshold at which a replenishment PO is auto-triggered."),
  unit_cost_usd     FLOAT64 OPTIONS(description="Latest unit cost in USD. Used for inventory valuation.")
) OPTIONS(description="On-hand inventory snapshot. One row per (SKU, warehouse). Source of stock levels, reorder triggers, and inventory value.");
SQL
ok "Schemas created"

step "Inserting sample rows"
bq --project_id="$PROJECT" --location="$BQ_LOCATION" query --use_legacy_sql=false --quiet <<SQL
INSERT INTO \`$PROJECT.$DATASET.suppliers\` VALUES
  ('SUP-001', 'Acme Components Ltd',   'Strategic', 'US', 'Net 30', DATE '2021-03-15'),
  ('SUP-002', 'Nordic Steel AB',       'Preferred', 'SE', 'Net 60', DATE '2022-07-01'),
  ('SUP-003', 'Pacific Logistics Co',  'Approved',  'JP', 'Net 45', DATE '2023-01-10'),
  ('SUP-004', 'Globex Manufacturing',  'Strategic', 'DE', 'Net 30', DATE '2020-11-20'),
  ('SUP-005', 'Initech Hardware',      'Approved',  'TW', 'Net 30', DATE '2024-02-05'),
  ('SUP-006', 'BlueRiver Plastics',    'Preferred', 'CN', 'Net 60', DATE '2022-04-18'),
  ('SUP-007', 'Vandelay Industries',   'Strategic', 'US', 'Net 30', DATE '2019-09-30'),
  ('SUP-008', 'Stark Electronics',     'Preferred', 'US', 'Net 30', DATE '2023-08-22');

INSERT INTO \`$PROJECT.$DATASET.purchase_orders\` VALUES
  ('PO-7001', 'SUP-001', 'Fulfilled',    180000.00, 'USD', DATE '2025-04-10', TIMESTAMP '2025-03-25 09:00:00', 'CC-OPS-101'),
  ('PO-7002', 'SUP-002', 'Acknowledged',  92500.00, 'EUR', DATE '2025-06-20', TIMESTAMP '2025-05-12 11:30:00', 'CC-MFG-220'),
  ('PO-7003', 'SUP-003', 'In Transit',    47000.00, 'JPY', DATE '2025-07-05', TIMESTAMP '2025-06-01 08:00:00', 'CC-OPS-101'),
  ('PO-7004', 'SUP-004', 'Fulfilled',    310000.00, 'EUR', DATE '2025-02-28', TIMESTAMP '2025-02-01 09:30:00', 'CC-MFG-220'),
  ('PO-7005', 'SUP-005', 'Cancelled',     14500.00, 'USD', DATE '2025-05-15', TIMESTAMP '2025-04-20 10:00:00', 'CC-OPS-101'),
  ('PO-7006', 'SUP-006', 'Issued',        65000.00, 'USD', DATE '2025-08-01', TIMESTAMP '2025-06-15 13:00:00', 'CC-MFG-220'),
  ('PO-7007', 'SUP-007', 'Fulfilled',    220000.00, 'USD', DATE '2025-05-10', TIMESTAMP '2025-04-12 09:00:00', 'CC-MFG-220'),
  ('PO-7008', 'SUP-008', 'Acknowledged',  88000.00, 'USD', DATE '2025-07-20', TIMESTAMP '2025-06-10 15:00:00', 'CC-OPS-101');

INSERT INTO \`$PROJECT.$DATASET.shipments\` VALUES
  ('SHIP-9001', 'PO-7001', 'FedEx',  'Delivered',  'US', 'WH-NA-01', TIMESTAMP '2025-04-08 09:00:00', TIMESTAMP '2025-04-09 14:30:00'),
  ('SHIP-9002', 'PO-7002', 'DHL',    'In Transit', 'SE', 'WH-EMEA-02', TIMESTAMP '2025-06-18 12:00:00', NULL),
  ('SHIP-9003', 'PO-7003', 'Maersk', 'In Transit', 'JP', 'WH-APAC-03', TIMESTAMP '2025-07-03 06:00:00', NULL),
  ('SHIP-9004', 'PO-7004', 'DHL',    'Delivered',  'DE', 'WH-EMEA-02', TIMESTAMP '2025-02-25 10:00:00', TIMESTAMP '2025-02-26 09:00:00'),
  ('SHIP-9005', 'PO-7007', 'UPS',    'Delivered',  'US', 'WH-NA-01', TIMESTAMP '2025-05-09 14:00:00', TIMESTAMP '2025-05-08 18:00:00'),
  ('SHIP-9006', 'PO-7007', 'UPS',    'Delayed',    'US', 'WH-NA-01', TIMESTAMP '2025-05-09 14:00:00', TIMESTAMP '2025-05-14 20:00:00');

INSERT INTO \`$PROJECT.$DATASET.warehouses\` VALUES
  ('WH-NA-01',   'Newark Hub',          'NA',    18000, TRUE),
  ('WH-EMEA-02', 'Rotterdam Gateway',   'EMEA',  22000, TRUE),
  ('WH-APAC-03', 'Singapore Cross-Dock','APAC',  12000, TRUE),
  ('WH-LATAM-04','Sao Paulo Center',    'LATAM',  9000, TRUE),
  ('WH-NA-05',   'Oakland Legacy',      'NA',     4500, FALSE);

INSERT INTO \`$PROJECT.$DATASET.inventory_items\` VALUES
  ('SKU-100', 'M4 carbon-steel bolt',          'WH-NA-01',    24500,  5000,   0.42),
  ('SKU-101', 'M4 stainless-steel bolt',       'WH-NA-01',     8200,  3000,   0.58),
  ('SKU-200', '12mm PETG sheet, 1m x 2m',      'WH-EMEA-02',    640,   200,  28.40),
  ('SKU-300', 'Aluminium extrusion, 1.5m',     'WH-EMEA-02',   1850,   500,  14.20),
  ('SKU-400', 'Lithium cell, 18650',           'WH-APAC-03',  62000, 10000,   3.85),
  ('SKU-500', 'Industrial bearing, 6202-ZZ',   'WH-NA-01',     1200,   400,   6.10),
  ('SKU-600', 'PCB blank, 4-layer',            'WH-APAC-03',   4800,  1500,   4.95);
SQL
ok "Rows inserted"

step "Creating bucket prefix"
if gsutil ls -b "gs://$BUCKET" >/dev/null 2>&1; then
  ok "Bucket already exists"
else
  gsutil mb -p "$PROJECT" "gs://$BUCKET"
  ok "Created gs://$BUCKET"
fi

step "Uploading domain markdown docs"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cat >"$TMP/01-procurement-glossary-draft.md" <<'MD'
# Procurement Glossary (working draft)

Living draft of the procurement-domain glossary. The data team will
turn this into the canonical glossary in Dataplex Knowledge Catalog.

## Supplier
A legal entity contracted to provide goods or services. Identified by a
stable `supplier_id`. Onboarded after compliance and credit review.

## Strategic Supplier
A Supplier whose `supplier_tier = 'Strategic'`. Single-source critical
components; highest engagement and contract review cadence.

## Approved Supplier
A Supplier whose `supplier_tier = 'Approved'`. Cleared for purchase but
not subject to QBRs or long-term capacity commitments.

## Purchase Order (PO)
A commercial commitment from us to a Supplier to buy a defined quantity
of goods/services at agreed prices and terms. Identified by `po_id`.
A PO progresses through statuses: Draft → Issued → Acknowledged →
Fulfilled (or Cancelled).

## Committed Spend
Sum of `total_amount_usd` for POs whose `po_status` is one of
'Issued', 'Acknowledged', or 'Fulfilled'. Excludes 'Draft' and
'Cancelled'.

## Lead Time
Days between PO `issued_at` and the latest Shipment's `actual_arrival`.
Tracked at the (supplier, SKU) level.

## On-Time Delivery (OTD)
Percentage of Shipments whose `actual_arrival <= estimated_arrival`,
out of Shipments with `shipment_status = 'Delivered'`.

## Stock-Out
A SKU at a Warehouse whose `quantity_on_hand = 0` AND
`reorder_point > 0`. Counted at the (SKU, warehouse) level.

## Cost Center
Internal accounting unit charged for the spend (`cost_center` on the
PO). Maps to a department / program in finance.
MD

cat >"$TMP/02-procurement-data-dictionary.md" <<'MD'
# Supply Chain — Data Dictionary

## Tables

| Table | What it represents |
| --- | --- |
| `suppliers` | Master of approved vendors. |
| `purchase_orders` | Committed buys against suppliers. |
| `shipments` | Inbound shipments fulfilling POs. |
| `warehouses` | Receiving / distribution locations. |
| `inventory_items` | On-hand stock per (SKU, warehouse). |

## Important columns

### suppliers.supplier_tier
- `Strategic`: critical, single-source, QBR cadence.
- `Preferred`: dual-source, capacity-reserved.
- `Approved`: cleared for use, spot-buy only.

### purchase_orders.po_status
Lifecycle:
- `Draft` — not yet sent to supplier.
- `Issued` — sent.
- `Acknowledged` — supplier confirmed.
- `Fulfilled` — all shipments delivered.
- `Cancelled` — terminated before fulfilment.

### purchase_orders.currency
Settlement currency. `total_amount_usd` is the USD-converted amount at
PO issuance for cross-currency comparison.

### shipments.shipment_status
- `In Transit` — picked up but not delivered.
- `Delivered` — receipt confirmed at the destination warehouse.
- `Delayed` — past `estimated_arrival` with no receipt yet.
- `Lost` — escalated to claims.

### inventory_items.reorder_point
Replenishment trigger. When `quantity_on_hand` falls below this number,
a new PO is auto-generated to the preferred supplier.
MD

cat >"$TMP/03-supplier-tiering.md" <<'MD'
# Supplier Tiering Policy

## Tier definitions

| Tier | Criteria | Engagement |
| --- | --- | --- |
| Strategic | Single-source, > $1M annual spend, critical path | Quarterly Business Review, multi-year contract |
| Preferred | Dual-source, $250K – $1M annual spend | Annual Business Review |
| Approved | Spot-buy, < $250K annual spend | No formal cadence |

## Re-tiering

Tier review runs annually. Triggers for off-cycle review:
- Quality incident (Critical defect) → automatic Strategic downgrade.
- Audit failure → suspended status until remediation.

## Onboarding

New suppliers move through: Sourcing → Risk Review → Finance Check →
Quality Audit → Onboarded. `suppliers.onboarded_date` records the date
the Quality Audit passed.
MD

cat >"$TMP/04-shipment-sla.md" <<'MD'
# Shipment SLA & Exception Policy

## SLA categories

| Lane type | Carrier OTD target | Internal OTD target |
| --- | --- | --- |
| Air | 95% | 92% |
| Ocean | 90% | 85% |
| Ground | 97% | 95% |

OTD = On-Time Delivery. See KPI definitions.

## Exception types

- `Delayed` — past estimated_arrival but not yet delivered. Auto-escalates
  after 48h.
- `Lost` — declared after 14 days with no carrier scans. Triggers
  insurance claim.
- `Damaged` — recorded at receipt; not in the current schema but
  planned.

## Carrier scorecard

Carriers are scored quarterly on OTD%, Exception Rate, and
Damage Rate. Carriers with two consecutive quarters below target are
de-listed.
MD

cat >"$TMP/05-supply-kpi-definitions.md" <<'MD'
# Supply Chain KPI Definitions

## On-Time Delivery Rate (OTD%)
`COUNT(shipments WHERE actual_arrival <= estimated_arrival) /
 COUNT(shipments WHERE shipment_status = 'Delivered')`.

## Lead Time (days)
`AVG(DATE_DIFF(actual_arrival, issued_at))` over delivered shipments,
grouped by (supplier, SKU). Reported as the trailing 90-day mean.

## Committed Spend
`SUM(total_amount_usd) WHERE po_status IN
 ('Issued', 'Acknowledged', 'Fulfilled')`. Reported by Cost Center.

## Inventory Turnover
`COGS_period / AVG(unit_cost_usd * quantity_on_hand)`. Higher is
better; flat or declining trend triggers a working-capital review.

## Stock-Out Rate
`COUNT(SKUs WHERE quantity_on_hand = 0 AND reorder_point > 0) /
 COUNT(SKUs WHERE reorder_point > 0)`.

## Supplier Concentration Risk
% of Committed Spend going to top-3 Strategic Suppliers. Board reports
weekly.
MD

gsutil -m cp "$TMP"/*.md "gs://$BUCKET/supply-chain/" >/dev/null
ok "Uploaded 5 markdown docs to gs://$BUCKET/supply-chain/"

step "Done"
cat <<EOF
BigQuery dataset: $PROJECT:$DATASET
Tables:
  - suppliers (8 rows)
  - purchase_orders (8 rows)
  - shipments (6 rows)
  - warehouses (5 rows)
  - inventory_items (7 rows)

GCS prefix: gs://$BUCKET/supply-chain/
Docs:
  - 01-procurement-glossary-draft.md
  - 02-procurement-data-dictionary.md
  - 03-supplier-tiering.md
  - 04-shipment-sla.md
  - 05-supply-kpi-definitions.md

Wait 5-15 min for Knowledge Catalog to index, then in the agent:

  Recommend a new glossary for our supply-chain domain in project
  $PROJECT, grounded in gs://$BUCKET/supply-chain/. Focus on
  procurement and logistics business concepts.

EOF
