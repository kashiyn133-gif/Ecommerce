"""
run_kpi_checks.py - M3 KPI Analyst (B)
======================================
Computes the M3 deliverables from the cleaned data and the database:

  reports/tables/kpi_crosscheck.csv        KPI | Python | SQL | difference | status
  reports/tables/rating_comparison.csv     avg rating + counts, on-time vs late
  reports/tables/rating_distribution.csv   % of 1-5 star ratings, on-time vs late
  reports/tables/segments_<seg>_<kpi>.csv  KPI by region / weekday / category / month
  reports/tables/hand_check_10_orders.csv  10 random orders to re-calculate in Excel
  reports/figures/08_rating_on_time_vs_late.png

Run from the project root with .venv active, after clean.py, ingest.py and
schema.sql / views.sql:

    python src/run_kpi_checks.py
    python src/run_kpi_checks.py --no-sql      # Python outputs only (no database)
"""

import argparse
import re
from pathlib import Path

import pandas as pd

import kpi

ROOT = Path(__file__).resolve().parent.parent
SQL_FILE = ROOT / "sql" / "kpi_queries.sql"
TABLES = ROOT / "reports" / "tables"
FIGURES = ROOT / "reports" / "figures"
TOLERANCE = 1e-6          # raw (unrounded) values must agree to this

SEGMENTS = ["region", "purchase_weekday", "category", "purchase_month"]


# ------------------------------------------------------------------
# SQL helpers
# ------------------------------------------------------------------
def load_named_queries(path=SQL_FILE):
    """Split kpi_queries.sql into {block_name: sql} using the '-- name:' markers."""
    text = Path(path).read_text()
    parts = re.split(r"^-- name:\s*(\w+)\s*$", text, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def run_sql_kpis(run_query, execute):
    """Run every block. run_query(sql) -> DataFrame, execute(sql) -> None."""
    blocks = load_named_queries()
    execute(blocks["setup_view"])
    frames = [run_query(sql) for name, sql in blocks.items() if name.startswith("kpi")]
    dist = run_query(blocks["extra_rating_distribution"])
    return pd.concat(frames, ignore_index=True), dist


def postgres_helpers():
    """Connect with the same .env settings as src/ingest.py."""
    import os
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL

    load_dotenv(ROOT / ".env")
    url = URL.create("postgresql+psycopg", username=os.getenv("DB_USER"),
                     password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"),
                     port=int(os.getenv("DB_PORT", "5432")), database=os.getenv("DB_NAME"))
    engine = create_engine(url)

    def run_query(sql):
        with engine.connect() as conn:
            return pd.read_sql(text(sql), conn)

    def execute(sql):
        with engine.begin() as conn:
            conn.execute(text(sql))

    return run_query, execute


# ------------------------------------------------------------------
# Python side
# ------------------------------------------------------------------
def python_kpis(orders, reviews):
    """Return a DataFrame (kpi, value, denominator) in the same shape as the SQL output."""
    rows = []
    for name, fn in kpi.ALL_SIMPLE_KPIS.items():
        r = fn(orders)
        rows.append((name, r.value, r.denominator))
    rc = kpi.rating_comparison(orders, reviews).set_index("delivery_group")
    rows.append(("avg_rating_on_time", rc.loc["on_time", "avg_rating"], int(rc.loc["on_time", "orders"])))
    rows.append(("avg_rating_late", rc.loc["late", "avg_rating"], int(rc.loc["late", "orders"])))
    return pd.DataFrame(rows, columns=["kpi", "value", "denominator"])


def crosscheck_table(py, sql):
    t = py.merge(sql, on="kpi", how="outer", suffixes=("_python", "_sql"))
    t["difference"] = t["value_python"] - t["value_sql"]
    t["denominator_match"] = t["denominator_python"] == t["denominator_sql"]
    ok = (t["difference"].abs() < TOLERANCE) & t["denominator_match"]
    t["status"] = ok.map({True: "match", False: "INVESTIGATE"})
    order = ["avg_delivery_duration_days", "avg_processing_time_days", "avg_rating_on_time",
             "avg_rating_late", "on_time_rate_pct", "cancellation_rate_pct", "avg_delay_days"]
    t["kpi"] = pd.Categorical(t["kpi"], order, ordered=True)
    return t.sort_values("kpi").reset_index(drop=True)


# ------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------
def rating_chart(dist, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 4.2))
    width = 0.38
    x = dist["review_score"].astype(int)
    ax.bar(x - width / 2, dist["on_time_pct"], width, label="On-time orders", color="#2a9d8f")
    ax.bar(x + width / 2, dist["late_pct"], width, label="Late orders", color="#e76f51")
    ax.set_xlabel("Review score (stars)")
    ax.set_ylabel("Share of orders in group (%)")
    ax.set_title("Rating distribution: on-time vs late delivered orders")
    ax.set_xticks(x)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def hand_check_sample(orders, n=10, seed=42):
    """10 random orders (mixed statuses) with the raw timestamps and Python results.

    Re-calculate the three right-hand columns in Excel and compare."""
    delivered = orders[(orders["order_status"] == "delivered")
                       & orders["order_delivered_customer_date"].notna()
                       & orders["order_delivered_carrier_date"].notna()]
    s = delivered.sample(n, random_state=seed)
    out = s[["order_id", "order_purchase_timestamp", "order_delivered_carrier_date",
             "order_delivered_customer_date", "order_estimated_delivery_date"]].copy()
    out["python_processing_days"] = kpi._days(s["order_delivered_carrier_date"], s["order_purchase_timestamp"])
    out["python_delivery_days"] = kpi._days(s["order_delivered_customer_date"], s["order_purchase_timestamp"])
    out["python_delay_days"] = kpi._delay_days(s)
    return out.reset_index(drop=True)


def main(use_sql=True):
    TABLES.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)

    orders = kpi.load_orders()
    orders = kpi.add_segments(orders, with_category=True)
    reviews = kpi.load_latest_reviews()

    # Our latest-review rule must equal M2's review_score_latest column in orders_clean.csv
    if "review_score_latest" in orders:
        mine = orders[["order_id"]].merge(reviews, on="order_id", how="left")["review_score"]
        same = (mine.fillna(-1).values == orders["review_score_latest"].fillna(-1).values).all()
        print(f"Latest-review rule matches clean.py review_score_latest: {same}")
        assert same, "kpi.py and clean.py pick different 'latest' reviews"

    # Sensitivity check: does the on-time vs late gap change if we average instead?
    gap_latest = kpi.rating_gap(orders, reviews)
    gap_mean = kpi.rating_gap(orders, kpi.load_reviews("mean"))
    print(f"Rating gap, latest rule: {gap_latest:.3f} | mean rule: {gap_mean:.3f}")

    # --- rating tables + chart (Python) ---
    rc = kpi.rating_comparison(orders, reviews)
    rc.to_csv(TABLES / "rating_comparison.csv", index=False)
    dist = kpi.rating_distribution(orders, reviews)
    dist.to_csv(TABLES / "rating_distribution.csv", index=False)
    rating_chart(dist, FIGURES / "08_rating_on_time_vs_late.png")
    print(rc.to_string(index=False))
    print(f"Rating gap (on-time minus late): {kpi.rating_gap(orders, reviews):.3f} points")

    # --- segment tables ---
    for seg in SEGMENTS:
        for name, fn in [("duration", kpi.avg_delivery_duration), ("processing", kpi.avg_processing_time)]:
            fn(orders, group_by=seg).to_csv(TABLES / f"segments_{seg}_{name}.csv", index=False)
        kpi.rating_comparison(orders, reviews, group_by=seg).to_csv(
            TABLES / f"segments_{seg}_rating.csv", index=False)

    # --- hand check ---
    hand_check_sample(orders).to_csv(TABLES / "hand_check_10_orders.csv", index=False)

    # --- exclusion counts (the denominators the guide asks us to report) ---
    for fn in (kpi.avg_delivery_duration, kpi.avg_processing_time):
        print(fn(orders), fn(orders).exclusion_reasons)
    n_rated = kpi.rated_orders(orders, reviews).attrs
    print(f"Rating KPI: {n_rated['eligible_delivered']:,} eligible delivered orders, "
          f"{n_rated['without_review']:,} without a review (excluded)")

    # --- Python vs SQL ---
    py = python_kpis(orders, reviews)
    if not use_sql:
        print(py.to_string(index=False))
        return
    run_query, execute = postgres_helpers()
    sql, sql_dist = run_sql_kpis(run_query, execute)
    table = crosscheck_table(py, sql)
    table.to_csv(TABLES / "kpi_crosscheck.csv", index=False)
    print(table.to_string(index=False))
    if (table["status"] != "match").any():
        raise SystemExit("Python and SQL disagree - investigate before reporting.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sql", action="store_true", help="skip the PostgreSQL cross-check")
    main(use_sql=not ap.parse_args().no_sql)
