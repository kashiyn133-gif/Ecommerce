# M3 KPI Definitions (KPI Analyst B)

**Owner:** M3 – KPI Analyst (B)
**Code:** `src/kpi.py` (Python), `sql/kpi_queries.sql` (SQL), `src/run_kpi_checks.py` (cross-check)
**Input:** `data/interim/orders_clean.csv` from `src/clean.py` (flag, don't delete: no order is removed; each KPI states its own exclusions).

> Status: DRAFT for agreement with M6 (shared definitions, function style). Fill the "Result" column from `reports/tables/kpi_crosscheck.csv` after running the pipeline on the real data.

## 1. KPI definitions

| # | KPI | Start | End | Unit | Population (denominator) | Excluded (and why) |
|---|---|---|---|---|---|---|
| 1 | Average delivery duration | `order_purchase_timestamp` | `order_delivered_customer_date` | days, decimal | `order_status = 'delivered'` and a customer delivery date | Non-delivered orders; delivered with no delivery date (DQ-01) |
| 2 | Average order processing time | `order_purchase_timestamp` | `order_delivered_carrier_date` | days, decimal | `delivered`, carrier date present, carrier date ≥ purchase date | Non-delivered; no carrier date (DQ-03); carrier before purchase (DQ-05, impossible) |
| 3 | Customer rating, on-time vs late | – | – | stars (1–5) | Delivered orders with a delivery date, an estimated date **and** at least one review | Non-delivered; missing dates; orders with no review |

Days are elapsed time: `(end − start) in seconds / 86,400`.

**Why these choices**

- *Duration starts at purchase, not approval.* The customer experiences the wait from the moment they order, and it matches the delivery-time definition already used in `docs/delivery_kpi_methodology.md` (12.56 days).
- *Processing time ends at carrier handover, starts at purchase.* The guide allows "dispatch or approval minus order placement"; purchase → dispatch is used because approval is missing for more orders (DQ-04) and the dispatch date is the one already used for `processing_days` in `clean.py`. Transit time is the rest: carrier → customer.
- *DQ-06 (carrier before approval, 1,359 orders) is kept in KPI 2* because the KPI starts at purchase, not approval. Its effect is small; check by excluding it as a sensitivity test.
- *Long deliveries (DQ-08) are kept in KPI 1*: they are real late orders, not errors. The mean is therefore sensitive to them; report the median as a supporting figure if needed.

## 2. Rating linkage rule (DQ-10) - DECISION: latest review
M2 proposed the "latest review per order" rule (docs/data_quality_audit.md section 2). M3 decision: CONFIRMED - use the latest review. Decision text for docs/decisions_log.md:
2). **M3 decision: CONFIRMED - use the latest review.** Please record it in `docs/decisions_log.md`:

> DQ-10 rating rule: use the latest review per order (`review_score_latest`). Ordering exactly as in clean.py: `review_answer_timestamp`, falling back to `review_creation_date`; a review with no date counts as oldest; ties by `review_creation_date`, then `review_id`. Reason: it reflects the customer's final opinion once the delivery outcome is known and gives one whole-number score per order, so the 1-5 star distribution stays valid. Sensitivity: `review_score_mean` was also compared; the result is in section 4.

- 547 orders have more than one review, and in 202 of them the scores disagree, so the rule can change results; this is why the mean is run as a sensitivity check (`run_kpi_checks.py` prints the on-time-minus-late gap under both rules).
- `kpi.py` rebuilds the latest review from the raw review table with the same ordering as `clean.py` and `run_kpi_checks.py` asserts it equals `review_score_latest` in `orders_clean.csv`. The SQL uses the same ordering.
- On-time = delivered calendar date <= estimated calendar date; late = after it. Compared: average rating and the share of 1-5 star ratings.
- Orders with no review (768 in the full data, any status) are excluded from this KPI only; the number of excluded delivered orders is printed by `run_kpi_checks.py`.
- Wording to use: *"late orders are **associated with** lower ratings."* The comparison is descriptive, not causal.

## 3. SQL versions of all six KPIs

`sql/kpi_queries.sql` uses the same populations and returns `(kpi, value, denominator)`. KPIs 4–6 follow M1's methodology and are provisional until M6 confirms.

| # | KPI | Definition used | Denominator |
|---|---|---|---|
| 1 | Avg delivery duration | above | delivered + delivery date |
| 2 | Avg processing time | above | delivered + valid carrier date |
| 3 | Avg rating on-time / late | above | rated eligible delivered orders in each group |
| 4 | On-time rate (M6) | % with delivered date ≤ estimated date | delivered + both dates |
| 5 | Cancellation rate (M6) | `canceled` orders / all orders | all orders |
| 6 | Average delay (M6) | mean(delivered date − estimated date) in calendar days; negative = early | delivered + both dates |

## 4. Cross-check (Python vs SQL)

`python src/run_kpi_checks.py` writes `reports/tables/kpi_crosscheck.csv` with: KPI, Python value, SQL value, difference, denominator match, status. Raw (unrounded) values must agree to 1e-6 and denominators must be identical; anything else is flagged `INVESTIGATE` and the script exits with an error.

| KPI | Python | SQL | Difference | Status |
|---|---:|---:|---:|---|
| Avg delivery duration (days) | _run to fill_ | | | |
| Avg processing time (days) | _run to fill_ | | | |
| Avg rating – on-time | _run to fill_ | | | |
| Avg rating – late | _run to fill_ | | | |
| On-time rate (%) | _run to fill_ | | | |
| Cancellation rate (%) | _run to fill_ | | | |
| Avg delay (days) | _run to fill_ | | | |

Expected from `docs/delivery_kpi_methodology.md` as a sanity check: delivery duration 12.56 days, on-time rate 93.23%, average delay −11.88 days, on 96,470 orders.

## 5. Segments (Week 3)

`kpi.avg_delivery_duration(df, group_by=...)` (and the other KPI functions) accept any of: `region` (5 Brazilian regions from `customer_state`), `state`, `purchase_weekday`, `purchase_month`, `category`. Each table shows orders in the segment, orders used (the denominator) and orders excluded. Category = category of the highest-priced item in the order (ties: lowest item id); no category → `unknown` (DQ-11), no items → `no_items` (DQ-09). **Carrier is not available**: the dataset has no carrier/courier identifier (`seller_id` is the seller, not the carrier), so no carrier segment is produced.

## 6. Hand check (10 orders)

`reports/tables/hand_check_10_orders.csv` lists 10 randomly sampled delivered orders (fixed seed) with raw timestamps. In Excel, recompute `(carrier − purchase)`, `(customer − purchase)` in days and the calendar-day delay, and compare with the `python_*` columns.

## 7. KPI limitations (draft paragraph for the report)

Durations are elapsed days between recorded timestamps in Brazil local time, with no time-zone conversion, so very short intervals are approximate. Processing time depends on the carrier handover timestamp, which is impossible for 166 orders (before purchase) and missing for others; these are excluded and counted. The rating KPI covers only delivered orders that were reviewed (a self-selected group), uses the latest review where several exist, and shows association, not causation. Averages are sensitive to the 4.9% of long deliveries that were kept on purpose. The dataset covers 2016–2018 and has no carrier field, so carrier-level analysis is not possible.
## Real-data cross-check

Run on the real data (`python src/run_kpi_checks.py`). Full output: `reports/tables/kpi_crosscheck.csv`.

| KPI | Python value (n) | SQL value (n) | Difference | Status |
|---|---|---|---|---|
| avg_delivery_duration_days | 12.558217 (96,470) | 12.558217 (96,470) | -1.07e-14 | match |
| avg_processing_time_days | 3.234903 (96,311) | 3.234903 (96,311) | 3.29e-14 | match |
| avg_rating_on_time | 4.290386 (89,443) | 4.290386 (89,443) | 0 | match |
| avg_rating_late | 2.270491 (6,381) | 2.270491 (6,381) | 0 | match |
| on_time_rate_pct | 93.226910 (96,470) | 93.226910 (96,470) | 0 | match |
| cancellation_rate_pct | 0.628513 (99,441) | 0.628513 (99,441) | 0 | match |
| avg_delay_days | -11.875889 (96,470) | -11.875889 (96,470) | 0 | match |

All seven comparisons match, with identical denominators and differences within 1e-6.

Rating KPI: 96,470 eligible delivered orders; 89,443 on-time and 6,381 late have a review; 646 delivered orders were excluded for missing reviews.

KPIs 4-6 (on-time rate, cancellation rate, average delay) remain provisional until M6 confirms their definitions.