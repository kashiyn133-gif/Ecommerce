"""
kpi.py - M3 KPI Analyst (B)
===========================
KPI functions for the Olist delivery project. Reads the cleaned order file
written by src/clean.py (data/interim/orders_clean.csv) plus the raw review,
item, product and translation tables (read only).

M3 KPIs (definitions: docs/kpi_definitions_m3.md)
    1. avg_delivery_duration   purchase -> customer delivery, in days
    2. avg_processing_time     purchase -> carrier handover, in days
    3. rating_comparison       average review score, on-time vs late orders

Provisional M6 KPIs (kept here only so Python can be cross-checked with the
SQL for all six KPIs; M6 owns the final definitions):
    4. on_time_rate, 5. cancellation_rate, 6. avg_delay

Every function returns the value AND its denominator. Pass group_by="region"
(or any column added by add_segments) to get one row per segment with counts.

Usage:
    from kpi import load_orders, avg_delivery_duration
    df = load_orders()
    print(avg_delivery_duration(df))
    print(avg_delivery_duration(df, group_by="region"))
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CLEAN_FILE = ROOT / "data" / "interim" / "orders_clean.csv"

DATE_COLS = [
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
]

REGION_OF_STATE = {
    **dict.fromkeys(["AC", "AP", "AM", "PA", "RO", "RR", "TO"], "North"),
    **dict.fromkeys(["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"], "Northeast"),
    **dict.fromkeys(["DF", "GO", "MT", "MS"], "Central-West"),
    **dict.fromkeys(["ES", "MG", "RJ", "SP"], "Southeast"),
    **dict.fromkeys(["PR", "RS", "SC"], "South"),
}


@dataclass
class KPIResult:
    name: str
    value: float
    denominator: int          # orders that entered the KPI
    excluded: int             # orders left out of this KPI (all reasons)
    exclusion_reasons: dict   # reason -> count (a row can appear under several reasons)
    unit: str

    def __str__(self):
        return (f"{self.name}: {self.value:.4f} {self.unit} "
                f"(n={self.denominator:,}, excluded={self.excluded:,})")


# ------------------------------------------------------------------
# Loading
# ------------------------------------------------------------------
def load_orders(path=CLEAN_FILE):
    """Cleaned order-level table (one row per order) with parsed dates + segments."""
    df = pd.read_csv(path, parse_dates=DATE_COLS)
    return add_segments(df)


def load_reviews(rule="latest", path=RAW / "olist_order_reviews_dataset.csv"):
    """One review score per order. rule = "latest" (chosen rule) or "mean" (sensitivity check).

    "latest" uses the SAME ordering as M2's clean.py (column review_score_latest):
      1. review_answer_timestamp, falling back to review_creation_date if missing;
      2. a review with neither date counts as the OLDEST;
      3. ties: review_creation_date, then review_id (all higher = later).
    The SQL in kpi_queries.sql uses the identical ordering.
    """
    r = pd.read_csv(path, parse_dates=["review_creation_date", "review_answer_timestamp"])
    if rule == "mean":
        m = r.groupby("order_id", as_index=False)["review_score"].mean()
        return m[["order_id", "review_score"]]
    if rule != "latest":
        raise ValueError("rule must be 'latest' or 'mean'")
    r["sort_time"] = r["review_answer_timestamp"].fillna(r["review_creation_date"])
    r = r.sort_values(["order_id", "sort_time", "review_creation_date", "review_id"],
                      na_position="first")
    latest = r.drop_duplicates("order_id", keep="last")
    return latest[["order_id", "review_score"]]


def load_latest_reviews(path=RAW / "olist_order_reviews_dataset.csv"):
    """Shortcut for load_reviews("latest")."""
    return load_reviews("latest", path)


def load_primary_category(raw=RAW):
    """Category of the highest-priced item in each order (ties: lowest item id).

    Orders without items -> 'no_items'. Products without a category -> 'unknown'
    (rule DQ-11). Categories without an English name keep the Portuguese name (DQ-12).
    """
    items = pd.read_csv(raw / "olist_order_items_dataset.csv")
    products = pd.read_csv(raw / "olist_products_dataset.csv")
    tr = pd.read_csv(raw / "product_category_name_translation.csv")

    items = items.sort_values(["order_id", "price", "order_item_id"],
                              ascending=[True, False, True])
    top = items.drop_duplicates("order_id", keep="first")
    top = top.merge(products[["product_id", "product_category_name"]],
                    on="product_id", how="left", validate="m:1")
    top = top.merge(tr, on="product_category_name", how="left", validate="m:1")
    top["category"] = (top["product_category_name_english"]
                       .fillna(top["product_category_name"])
                       .fillna("unknown"))
    return top.set_index("order_id")["category"]


def add_segments(df, with_category=False):
    """Add segment columns usable in group_by. Never changes the row count."""
    n = len(df)
    df = df.copy()
    df["state"] = df["customer_state"]
    df["region"] = df["customer_state"].map(REGION_OF_STATE).fillna("unknown")
    df["purchase_weekday"] = df["order_purchase_timestamp"].dt.day_name()
    df["purchase_month"] = df["order_purchase_timestamp"].dt.strftime("%Y-%m")
    if with_category:
        df["category"] = df["order_id"].map(load_primary_category()).fillna("no_items")
    assert len(df) == n
    return df


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _days(later, earlier):
    return (later - earlier).dt.total_seconds() / 86400


def _delay_days(df):
    """Calendar-day difference, actual minus estimated (same as M1's methodology)."""
    return (df["order_delivered_customer_date"].dt.normalize()
            - df["order_estimated_delivery_date"].dt.normalize()).dt.days


def _finish(df, name, mask, values, unit, reasons, group_by, agg="mean"):
    """Build KPIResult (no group_by) or a segment table (group_by)."""
    if group_by is None:
        v = values[mask]
        value = v.mean() if agg == "mean" else v.sum()
        counts = {k: int(m.sum()) for k, m in reasons.items()}
        return KPIResult(name, float(value) if len(v) else float("nan"),
                         int(mask.sum()), int((~mask).sum()), counts, unit)

    tmp = pd.DataFrame({"seg": df[group_by], "ok": mask, "v": values.where(mask)})
    g = tmp.groupby("seg", dropna=False)
    out = pd.DataFrame({
        "orders_in_segment": g.size(),
        "orders_used": g["ok"].sum().astype(int),
        name: g["v"].mean(),
    })
    out["orders_excluded"] = out["orders_in_segment"] - out["orders_used"]
    out = out.reset_index().rename(columns={"seg": group_by})
    return out.sort_values("orders_used", ascending=False).reset_index(drop=True)


# ------------------------------------------------------------------
# KPI 1 - Average delivery duration (days)
# ------------------------------------------------------------------
def avg_delivery_duration(df, group_by=None):
    """Mean of (customer delivery date - purchase timestamp), in days (decimal).

    Population: status 'delivered' AND a customer delivery date.
    Excluded: non-delivered orders; delivered orders with no delivery date (DQ-01).
    """
    delivered = df["order_status"] == "delivered"
    has_date = df["order_delivered_customer_date"].notna()
    mask = delivered & has_date
    values = _days(df["order_delivered_customer_date"], df["order_purchase_timestamp"])
    reasons = {"not delivered": ~delivered,
               "delivered but no delivery date (DQ-01)": delivered & ~has_date}
    return _finish(df, "avg_delivery_duration_days", mask, values, "days", reasons, group_by)


# ------------------------------------------------------------------
# KPI 2 - Average order processing time (days)
# ------------------------------------------------------------------
def avg_processing_time(df, group_by=None):
    """Mean of (carrier handover timestamp - purchase timestamp), in days (decimal).

    Population: status 'delivered' AND a carrier date AND carrier date >= purchase.
    Excluded: non-delivered; no carrier date (DQ-03); carrier before purchase (DQ-05).
    """
    delivered = df["order_status"] == "delivered"
    has_carrier = df["order_delivered_carrier_date"].notna()
    before = df["order_delivered_carrier_date"] < df["order_purchase_timestamp"]
    mask = delivered & has_carrier & ~before
    values = _days(df["order_delivered_carrier_date"], df["order_purchase_timestamp"])
    reasons = {"not delivered": ~delivered,
               "delivered but no carrier date (DQ-03)": delivered & ~has_carrier,
               "carrier date before purchase (DQ-05)": delivered & before}
    return _finish(df, "avg_processing_time_days", mask, values, "days", reasons, group_by)


# ------------------------------------------------------------------
# KPI 3 - Customer rating: on-time vs late delivered orders
# ------------------------------------------------------------------
def rated_orders(df, latest_reviews):
    """Delivered orders with a delivery date, an estimate and a review, + on_time flag."""
    base = df[(df["order_status"] == "delivered")
              & df["order_delivered_customer_date"].notna()
              & df["order_estimated_delivery_date"].notna()].copy()
    base["delay_days"] = _delay_days(base)
    base["on_time"] = base["delay_days"] <= 0
    base["delivery_group"] = base["on_time"].map({True: "on_time", False: "late"})
    n_base = len(base)
    out = base.merge(latest_reviews, on="order_id", how="inner", validate="1:1")
    out.attrs["without_review"] = n_base - len(out)
    out.attrs["eligible_delivered"] = n_base
    return out


def rating_comparison(df, latest_reviews, group_by=None):
    """Average rating (latest review per order) for on-time vs late orders.

    Returns one row per delivery_group (or per segment x group) with order counts.
    Wording for reports: 'late orders are ASSOCIATED with lower ratings' (not causal).
    """
    r = rated_orders(df, latest_reviews)
    keys = ["delivery_group"] if group_by is None else [group_by, "delivery_group"]
    out = (r.groupby(keys, dropna=False)["review_score"]
             .agg(orders="size", avg_rating="mean").reset_index())
    return out


def rating_distribution(df, latest_reviews):
    """Share of 1-5 star ratings for on-time vs late orders (% within each group)."""
    r = rated_orders(df, latest_reviews)
    counts = pd.crosstab(r["review_score"], r["delivery_group"])
    for col in ("on_time", "late"):
        if col not in counts:
            counts[col] = 0
    pct = counts / counts.sum() * 100
    out = counts.add_suffix("_orders").join(pct.add_suffix("_pct")).reset_index()
    return out


def rating_gap(df, latest_reviews):
    """avg rating on-time minus avg rating late (points on the 1-5 scale)."""
    t = rating_comparison(df, latest_reviews).set_index("delivery_group")["avg_rating"]
    return float(t["on_time"] - t["late"])


# ------------------------------------------------------------------
# KPIs 4-6 - provisional M6 definitions (for the six-KPI cross-check)
# ------------------------------------------------------------------
def _delay_population(df):
    delivered = df["order_status"] == "delivered"
    has = df["order_delivered_customer_date"].notna() & df["order_estimated_delivery_date"].notna()
    return delivered & has, delivered


def on_time_rate(df, group_by=None):
    """% of eligible delivered orders delivered on or before the estimated date."""
    mask, delivered = _delay_population(df)
    delay = _delay_days(df)
    values = (delay <= 0).astype(float) * 100
    reasons = {"not delivered": ~delivered, "delivered but missing a date": delivered & ~mask}
    return _finish(df, "on_time_rate_pct", mask, values, "%", reasons, group_by)


def avg_delay(df, group_by=None):
    """Mean of (actual - estimated delivery calendar date), in days. Negative = early."""
    mask, delivered = _delay_population(df)
    values = _delay_days(df).astype(float)
    reasons = {"not delivered": ~delivered, "delivered but missing a date": delivered & ~mask}
    return _finish(df, "avg_delay_days", mask, values, "days", reasons, group_by)


def cancellation_rate(df, group_by=None):
    """% of ALL orders with status 'canceled' (denominator = every order)."""
    mask = pd.Series(True, index=df.index)
    values = (df["order_status"] == "canceled").astype(float) * 100
    return _finish(df, "cancellation_rate_pct", mask, values, "%", {}, group_by)


ALL_SIMPLE_KPIS = {
    "avg_delivery_duration_days": avg_delivery_duration,
    "avg_processing_time_days": avg_processing_time,
    "on_time_rate_pct": on_time_rate,
    "cancellation_rate_pct": cancellation_rate,
    "avg_delay_days": avg_delay,
}


if __name__ == "__main__":
    orders = load_orders()
    reviews = load_latest_reviews()
    for fn in ALL_SIMPLE_KPIS.values():
        print(fn(orders))
    print(rating_comparison(orders, reviews))
    print(f"rating gap (on-time minus late): {rating_gap(orders, reviews):.3f}")
