# Data Quality Audit

**Project:** E-commerce Delivery & Logistics Performance Analysis
**Owner:** M2 – Data Quality Analyst
**Source:** Brazilian E-Commerce Public Dataset by Olist (see `docs/dataset_provenance.md`)
**Evidence:** every number below comes from `notebooks/00_data_audit.ipynb`

## 1. Before / After Summary

| Stage | Orders |
|---|---:|
| Raw orders (`olist_orders_dataset.csv`) | 99,441 |
| Orders flagged by at least one data-quality rule (DQ-01 to DQ-07, DQ-09) | 2,186 (2.20%) |
| Orders excluded | **0** |
| Orders in cleaned dataset (`data/interim/orders_clean.csv`) | 99,441 |

Long deliveries (DQ-08, 4,896 orders) are flagged separately and not counted above, because they are real late orders, not data errors.

**Policy: flag, don't delete.** No record is removed. Every rule adds a True/False flag column, so each KPI owner (M3, M6) can decide which flags to exclude for their KPI and must report that exclusion as part of the denominator.

## 2. Data Quality Rules

Percentages are of all 99,441 orders unless stated otherwise.

| Rule ID | Description | Rows affected | % of total | Action | Reason |
|---|---|---:|---:|---|---|
| DQ-01 | Status `delivered` but no customer delivery date | 8 | 0.01% | Flag `flag_delivered_no_delivery_date` | Delivery time and on-time status cannot be measured |
| DQ-02 | Status `canceled` but has a customer delivery date | 6 | 0.01% | Flag `flag_canceled_with_delivery_date` | Status and timestamp contradict each other |
| DQ-03 | Status `delivered` but no carrier (dispatch) date | 2 | <0.01% | Flag `flag_delivered_no_carrier_date` | Processing and transit time cannot be split |
| DQ-04 | Status `delivered` but no approval date | 14 | 0.01% | Flag `flag_delivered_no_approval_date` | Processing time from approval cannot be measured |
| DQ-05 | Carrier date earlier than purchase date | 166 | 0.17% | Flag `flag_carrier_before_purchase` | Impossible order of events |
| DQ-06 | Carrier date earlier than approval date | 1,359 | 1.37% | Flag `flag_carrier_before_approval` | Suspicious, likely system timing; affects processing-time KPI |
| DQ-07 | Customer delivery date earlier than carrier date | 23 | 0.02% | Flag `flag_delivered_before_carrier` | Impossible order of events |
| DQ-08 | Delivery time above IQR upper limit (29.15 days) | 4,896 | 4.92% | Flag `flag_long_delivery` (**keep**) | Real long deliveries, not errors; core to delay analysis |
| DQ-09 | Order has no items in `order_items` | 775 | 0.78% | Flag `flag_no_items` | No price or freight available for these orders |
| DQ-10 | Order has more than one review | 547 | 0.55% | Add `review_count` column | Rating rule to be decided by M3 (e.g. latest review) |
| DQ-11 | Product has no category | 610 products | 1.85% of products | Label as `unknown` in analysis | Never drop; keep as its own segment |
| DQ-12 | Category has no English translation (`pc_gamer`, `portateis_cozinha_e_preparadores_de_alimentos`) | 13 products | 0.04% of products | Keep Portuguese name | Translation table is incomplete |
| DQ-13 | Item freight value is 0 | 383 items | 0.34% of items | Keep (informational) | Plausible free shipping, not an error |

## 3. Checks Passed (no action needed)

| Check | Result |
|---|---|
| Exact duplicate rows (7 tables) | 0 in every table |
| Duplicate `order_id` in orders | 0 → one row per order confirmed |
| Status labels | 8 consistent lowercase values |
| Date parsing (5 columns, format `YYYY-MM-DD HH:MM:SS`) | 0 failures |
| Approval before purchase / delivery before purchase / estimate before purchase | 0 each |
| Review scores outside 1–5 | 0 |
| Price ≤ 0 or negative freight | 0 |
| Orders without a customer / items without an order / reviews without an order | 0 each |
| Customer state codes | 27 clean codes (26 states + DF) |

## 4. Missing Values Considered Normal

| Column | Missing | Why it is acceptable |
|---|---:|---|
| `review_comment_title` | 87,656 (88.34%) | Optional free-text field |
| `review_comment_message` | 58,247 (58.70%) | Optional free-text field |
| `order_delivered_customer_date` (non-delivered orders) | 2,957 | Orders that never reached the customer |
| `order_delivered_carrier_date` (non-dispatched orders) | 1,781 | Orders that never reached the courier |
| Product weight / dimensions | 2 products | Not used in Project 1 |

## 5. Notes and Limitations

- **Raw data unchanged:** SHA-256 fingerprints of all 9 raw files are stored in `docs/raw_file_checksums.txt`.
- **Geolocation table not used:** the project has no distance field, and one ZIP prefix maps to many coordinates.
- **Timezone:** timestamps are kept as stored in the source (Brazil local time); no conversion applied.
- **Source column misspellings:** `product_name_lenght` and `product_description_lenght` are misspelled in the original Olist data and left unchanged.
- **Ratings:** 768 orders have no review; 814 `review_id`s are shared across several orders bought together.
- Flags describe data issues only. They do not imply causes of delivery performance.