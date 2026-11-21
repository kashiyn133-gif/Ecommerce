"""
clean.py - M2 Data Quality Analyst
==================================
Reads the raw Olist files, applies the rules documented in
docs/data_quality_audit.md, and writes ONE ROW PER ORDER to
data/interim/orders_clean.csv.

Policy: flag, don't delete.
- Raw files in data/raw/ are only read, never changed.
- No order is removed. Every rule adds a True/False flag column,
  so KPI owners (M3, M6) decide what to exclude for each KPI.

How to run (from the project root, with .venv active):
    python src/clean.py
"""

from pathlib import Path

import pandas as pd

# ------------------------------------------------------------------
# 1. Paths and settings
# ------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent   # project root (one level above src/)
RAW = ROOT / "data" / "raw"                      # original files: read only
INTERIM = ROOT / "data" / "interim"              # cleaned output goes here
OUTPUT_FILE = INTERIM / "orders_clean.csv"

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"                # one format for every date column
DATE_COLS = [
    "order_purchase_timestamp",       # (1) customer buys
    "order_approved_at",              # (2) payment approved
    "order_delivered_carrier_date",   # (3) handed to courier
    "order_delivered_customer_date",  # (4) customer receives
    "order_estimated_delivery_date",  # promised date
]


# ------------------------------------------------------------------
# 2. Load raw data
# ------------------------------------------------------------------
def load_raw():
    """Read the 4 raw tables this script needs."""
    orders = pd.read_csv(RAW / "olist_orders_dataset.csv")
    items = pd.read_csv(RAW / "olist_order_items_dataset.csv")
    reviews = pd.read_csv(RAW / "olist_order_reviews_dataset.csv")
    customers = pd.read_csv(RAW / "olist_customers_dataset.csv")
    return orders, items, reviews, customers


# ------------------------------------------------------------------
# 3. Standardise types and text
# ------------------------------------------------------------------
def standardise(orders, customers):
    """Parse dates with one format and tidy text columns."""
    orders = orders.copy()        # work on a copy, never on the loaded raw table
    customers = customers.copy()

    for col in DATE_COLS:
        before_missing = orders[col].isna().sum()
        orders[col] = pd.to_datetime(orders[col], format=DATE_FORMAT, errors="coerce")
        failed = orders[col].isna().sum() - before_missing
        if failed > 0:            # audit found 0 failures; warn loudly if the data ever changes
            print(f"WARNING: {failed} values in {col} could not be parsed")

    orders["order_status"] = orders["order_status"].str.strip().str.lower()
    customers["customer_state"] = customers["customer_state"].str.strip().str.upper()
    customers["customer_city"] = customers["customer_city"].str.strip().str.lower()
    return orders, customers


# ------------------------------------------------------------------
# 4. Add one flag column per audit rule
# ------------------------------------------------------------------
def add_flags(df):
    """Add True/False flags for rules DQ-01 to DQ-08 (see the audit doc)."""
    delivered = df["order_status"] == "delivered"
    canceled = df["order_status"] == "canceled"

    purchase = df["order_purchase_timestamp"]
    approved = df["order_approved_at"]
    carrier = df["order_delivered_carrier_date"]
    customer = df["order_delivered_customer_date"]

    # Status vs dates (comparisons with a missing date give False)
    df["flag_delivered_no_delivery_date"] = delivered & customer.isna()   # DQ-01
    df["flag_canceled_with_delivery_date"] = canceled & customer.notna()  # DQ-02
    df["flag_delivered_no_carrier_date"] = delivered & carrier.isna()     # DQ-03
    df["flag_delivered_no_approval_date"] = delivered & approved.isna()   # DQ-04

    # Impossible order of events
    df["flag_carrier_before_purchase"] = carrier < purchase               # DQ-05
    df["flag_carrier_before_approval"] = carrier < approved               # DQ-06
    df["flag_delivered_before_carrier"] = customer < carrier              # DQ-07

    # Helper durations in days (86,400 seconds = 1 day), used for EDA.
    # Final KPI definitions belong to M3/M6.
    df["delivery_days"] = (customer - purchase).dt.total_seconds() / 86400
    df["processing_days"] = (carrier - purchase).dt.total_seconds() / 86400

    # DQ-08: long deliveries by the IQR rule, measured on delivered orders with a date.
    # Kept on purpose: these are real late orders, not errors.
    valid = delivered & customer.notna()
    q1, q3 = df.loc[valid, "delivery_days"].quantile([0.25, 0.75])
    upper = q3 + 1.5 * (q3 - q1)
    df["flag_long_delivery"] = valid & (df["delivery_days"] > upper)
    return df, upper


# ------------------------------------------------------------------
# 5. Build the order-level dataset
# ------------------------------------------------------------------
def build_clean(orders, items, reviews, customers):
    df, iqr_upper = add_flags(orders)

    # Count items and reviews per order with .map (adds a column, never adds rows)
    item_counts = items.groupby("order_id").size()
    review_counts = reviews.groupby("order_id").size()
    df["item_count"] = df["order_id"].map(item_counts).fillna(0).astype(int)
    df["review_count"] = df["order_id"].map(review_counts).fillna(0).astype(int)
    df["flag_no_items"] = df["item_count"] == 0                           # DQ-09
    # DQ-10: review_count > 1 marks orders with several reviews; M3 picks the rule

    # Add customer location; validate="m:1" stops the script if a join would duplicate orders
    df = df.merge(
        customers[["customer_id", "customer_city", "customer_state"]],
        on="customer_id", how="left", validate="m:1",
    )

    # One summary flag for "has at least one data-quality issue" (DQ-08 is not an error, so it is excluded)
    quality_flags = [
        "flag_delivered_no_delivery_date", "flag_canceled_with_delivery_date",
        "flag_delivered_no_carrier_date", "flag_delivered_no_approval_date",
        "flag_carrier_before_purchase", "flag_carrier_before_approval",
        "flag_delivered_before_carrier", "flag_no_items",
    ]
    df["flag_any_quality_issue"] = df[quality_flags].any(axis=1)
    return df, iqr_upper, quality_flags


# ------------------------------------------------------------------
# 6. Run, check, save, report
# ------------------------------------------------------------------
def main():
    orders, items, reviews, customers = load_raw()
    raw_rows = len(orders)

    orders, customers = standardise(orders, customers)
    df, iqr_upper, quality_flags = build_clean(orders, items, reviews, customers)

    # Safety checks: no order lost, none duplicated
    assert len(df) == raw_rows, f"Row count changed: {raw_rows} -> {len(df)}"
    assert df["order_id"].is_unique, "Duplicate order_id after cleaning"

    INTERIM.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_FILE, index=False)

    # Report (copy these numbers into docs/data_quality_audit.md)
    print("\nBEFORE / AFTER")
    print("-" * 50)
    print(f"Raw orders:                       {raw_rows:>7,}")
    print(f"Orders with any quality flag:     {df['flag_any_quality_issue'].sum():>7,}")
    print(f"Orders excluded:                  {0:>7,}")
    print(f"Orders in cleaned dataset:        {len(df):>7,}")

    print("\nFLAG COUNTS")
    print("-" * 50)
    for col in quality_flags + ["flag_long_delivery"]:
        print(f"{col:35} {df[col].sum():>6,}")
    print(f"{'orders with review_count > 1':35} {(df['review_count'] > 1).sum():>6,}")
    print(f"\nIQR upper limit for long delivery: {iqr_upper:.2f} days")
    print(f"Saved: {OUTPUT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()