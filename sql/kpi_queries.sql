-- =====================================================
-- E-commerce Delivery & Logistics Performance Analysis
-- File: kpi_queries.sql   (M3 KPI Analyst B)
-- Purpose: independent SQL calculation of all six KPIs,
--          to cross-check the Python results in src/kpi.py.
--
-- Every query returns rows of (kpi, value, denominator) so that
-- src/run_kpi_checks.py can compare them with Python.
-- Blocks start with "-- name: <block_name>"; run the whole file with psql,
-- or one block at a time.
--
-- Definitions: docs/kpi_definitions_m3.md
-- Dates are stored as text in the raw tables, so they are cast here
-- exactly as in views.sql.
-- =====================================================


-- name: setup_view
-- Shared order-level view: typed timestamps + the three derived measures.
CREATE OR REPLACE VIEW vw_kpi_orders AS
WITH base AS (
    SELECT
        order_id,
        order_status,
        NULLIF(order_purchase_timestamp, '')::TIMESTAMP        AS purchase_ts,
        NULLIF(order_delivered_carrier_date, '')::TIMESTAMP    AS carrier_ts,
        NULLIF(order_delivered_customer_date, '')::TIMESTAMP   AS customer_ts,
        NULLIF(order_estimated_delivery_date, '')::TIMESTAMP   AS estimated_ts
    FROM orders
)
SELECT
    order_id, order_status, purchase_ts, carrier_ts, customer_ts, estimated_ts,
    -- elapsed days, decimal (86,400 seconds = 1 day)
    (EXTRACT(EPOCH FROM (customer_ts - purchase_ts)) / 86400.0)::DOUBLE PRECISION AS delivery_days,
    (EXTRACT(EPOCH FROM (carrier_ts  - purchase_ts)) / 86400.0)::DOUBLE PRECISION AS processing_days,
    -- calendar-day difference, actual minus estimated (negative = early)
    (customer_ts::DATE - estimated_ts::DATE) AS delay_days
FROM base;


-- name: kpi1_avg_delivery_duration
-- KPI 1: average delivery duration (days). Delivered orders with a delivery date.
SELECT
    'avg_delivery_duration_days' AS kpi,
    AVG(delivery_days)::DOUBLE PRECISION AS value,
    COUNT(*) AS denominator
FROM vw_kpi_orders
WHERE order_status = 'delivered'
  AND customer_ts IS NOT NULL;


-- name: kpi2_avg_processing_time
-- KPI 2: average order processing time (days), purchase -> carrier handover.
-- Excludes missing carrier dates and carrier dates earlier than purchase (DQ-03, DQ-05).
SELECT
    'avg_processing_time_days' AS kpi,
    AVG(processing_days)::DOUBLE PRECISION AS value,
    COUNT(*) AS denominator
FROM vw_kpi_orders
WHERE order_status = 'delivered'
  AND carrier_ts IS NOT NULL
  AND carrier_ts >= purchase_ts;


-- name: kpi3_rating_on_time_vs_late
-- KPI 3: average rating of on-time vs late delivered orders.
-- Rule: ONE review per order = the latest, same ordering as M2's clean.py:
-- answer timestamp (fallback creation date; no date = oldest), then creation date, then review_id.
WITH latest AS (
    SELECT
        order_id,
        review_score,
        ROW_NUMBER() OVER (
            PARTITION BY order_id
            ORDER BY COALESCE(NULLIF(review_answer_timestamp, '')::TIMESTAMP,
                              NULLIF(review_creation_date, '')::TIMESTAMP) DESC NULLS LAST,
                     NULLIF(review_creation_date, '')::TIMESTAMP             DESC NULLS LAST,
                     review_id                                               DESC
        ) AS rn
    FROM reviews
),
rated AS (
    SELECT k.order_id, l.review_score, (k.delay_days <= 0) AS on_time
    FROM vw_kpi_orders k
    JOIN latest l ON l.order_id = k.order_id AND l.rn = 1
    WHERE k.order_status = 'delivered'
      AND k.customer_ts IS NOT NULL
      AND k.estimated_ts IS NOT NULL
)
SELECT
    CASE WHEN on_time THEN 'avg_rating_on_time' ELSE 'avg_rating_late' END AS kpi,
    AVG(review_score)::DOUBLE PRECISION AS value,
    COUNT(*) AS denominator
FROM rated
GROUP BY on_time
ORDER BY on_time DESC;


-- name: kpi4_on_time_rate
-- KPI 4 (M6): on-time delivery rate, % of eligible delivered orders.
SELECT
    'on_time_rate_pct' AS kpi,
    (100.0 * AVG((delay_days <= 0)::INT))::DOUBLE PRECISION AS value,
    COUNT(*) AS denominator
FROM vw_kpi_orders
WHERE order_status = 'delivered'
  AND customer_ts IS NOT NULL
  AND estimated_ts IS NOT NULL;


-- name: kpi5_cancellation_rate
-- KPI 5 (M6): cancellation rate, % of ALL orders.
SELECT
    'cancellation_rate_pct' AS kpi,
    (100.0 * SUM((order_status = 'canceled')::INT) / COUNT(*))::DOUBLE PRECISION AS value,
    COUNT(*) AS denominator
FROM vw_kpi_orders;


-- name: kpi6_avg_delay
-- KPI 6 (M6): average delay in calendar days (actual - estimated; negative = early).
SELECT
    'avg_delay_days' AS kpi,
    AVG(delay_days)::DOUBLE PRECISION AS value,
    COUNT(*) AS denominator
FROM vw_kpi_orders
WHERE order_status = 'delivered'
  AND customer_ts IS NOT NULL
  AND estimated_ts IS NOT NULL;


-- name: extra_rating_distribution
-- Supporting table for the rating chart: star counts for on-time vs late orders.
WITH latest AS (
    SELECT order_id, review_score,
           ROW_NUMBER() OVER (
               PARTITION BY order_id
               ORDER BY COALESCE(NULLIF(review_answer_timestamp, '')::TIMESTAMP,
                                 NULLIF(review_creation_date, '')::TIMESTAMP) DESC NULLS LAST,
                        NULLIF(review_creation_date, '')::TIMESTAMP             DESC NULLS LAST,
                        review_id                                               DESC
           ) AS rn
    FROM reviews
)
SELECT
    l.review_score,
    SUM((k.delay_days <= 0)::INT)     AS on_time_orders,
    SUM((k.delay_days > 0)::INT)      AS late_orders
FROM vw_kpi_orders k
JOIN latest l ON l.order_id = k.order_id AND l.rn = 1
WHERE k.order_status = 'delivered'
  AND k.customer_ts IS NOT NULL
  AND k.estimated_ts IS NOT NULL
GROUP BY l.review_score
ORDER BY l.review_score;
