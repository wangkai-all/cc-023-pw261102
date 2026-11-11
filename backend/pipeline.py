"""Point-in-time correct feature / label pipeline, executed in DuckDB.

Correct semantics (no leakage):
  1. Per (sample, event_id) pick the NEWEST revision whose ingest_time is
     already available at the decision moment (prediction_time for features,
     observation_cutoff for labels).
  2. Only then test the event's business time against the window.
  3. Feature window = [t - 7d, t): left-inclusive, right-exclusive. Events at
     exactly t are excluded.
  4. Label window = [t, t + 7d); if the declared observation cutoff does not
     cover t + 7d, the label is NULL and label_mature = FALSE.
  5. Revisions / late data arriving after the decision moment cannot flow
     backwards into earlier features.

The naive baseline deliberately joins ALL history (ignores ingest_time,
always takes the globally-latest revision) so its leaky numbers can be shown
side by side with the correct ones.
"""
from __future__ import annotations

import duckdb

from . import config

# ---------------------------------------------------------------------------
# 1. PAYMENT LINEAGE
#    One row per (sample, payment event, revision): every revision of every
#    payment of the sample's user, annotated with whether the correct logic
#    and the naive logic include it and why.
# ---------------------------------------------------------------------------
PAYMENT_LINEAGE_SQL = """
CREATE OR REPLACE TABLE payment_lineage AS
WITH joined AS (
    SELECT
        s.sample_id,
        s.user_id,
        s.prediction_time,
        s.prediction_time - INTERVAL 7 DAY AS window_start,
        p.event_id,
        p.version,
        p.event_time,
        p.ingest_time,
        p.status,
        p.amount,
        -- newest revision available AT prediction_time for this event
        MAX(p.version) FILTER (WHERE p.ingest_time <= s.prediction_time)
            OVER (PARTITION BY s.sample_id, p.event_id) AS latest_visible_version,
        -- newest revision across ALL history (what the naive join sees)
        MAX(p.version) OVER (PARTITION BY s.sample_id, p.event_id) AS latest_overall_version
    FROM samples_src s
    JOIN payments_src p ON p.user_id = s.user_id
),
classified AS (
    SELECT
        *,
        (version = latest_visible_version) AS is_latest_visible,
        (ingest_time <= prediction_time)   AS is_available,
        (event_time >= window_start)       AS after_window_start,
        (event_time <  prediction_time)    AS before_window_end,
        (status = 'ACTIVE')                AS is_active,
        (version = latest_overall_version) AS is_latest_overall
    FROM joined
)
SELECT
    *,
    CASE
        WHEN NOT is_available            THEN 'NOT_YET_AVAILABLE'
        WHEN NOT is_latest_visible       THEN 'SUPERSEDED_VERSION'
        WHEN NOT is_active               THEN 'REVOKED'
        WHEN NOT after_window_start      THEN 'OUTSIDE_BEFORE_WINDOW'
        WHEN NOT before_window_end       THEN 'OUTSIDE_AFTER_WINDOW'
        ELSE 'INCLUDED'
    END AS correct_decision,
    -- naive: globally-latest revision, active, business time in window,
    -- with NO availability check at all
    CASE
        WHEN NOT is_latest_overall       THEN 'SUPERSEDED_VERSION'
        WHEN NOT is_active               THEN 'REVOKED'
        WHEN NOT after_window_start      THEN 'OUTSIDE_BEFORE_WINDOW'
        WHEN NOT before_window_end       THEN 'OUTSIDE_AFTER_WINDOW'
        ELSE 'INCLUDED'
    END AS naive_decision,
    (correct_decision = 'INCLUDED') AS correct_included,
    (naive_decision  = 'INCLUDED') AS naive_included
FROM classified
"""

# ---------------------------------------------------------------------------
# 2. FEATURES (correct) + naive feature baseline
# ---------------------------------------------------------------------------
FEATURES_SQL = """
CREATE OR REPLACE TABLE features AS
WITH correct_agg AS (
    SELECT
        sample_id,
        COUNT(*) FILTER (WHERE correct_included)          AS pay_count_7d,
        COALESCE(SUM(amount) FILTER (WHERE correct_included), 0) AS pay_amount_7d,
        MAX(event_time) FILTER (WHERE correct_included)   AS last_pay_time
    FROM payment_lineage
    GROUP BY sample_id
),
naive_agg AS (
    SELECT
        sample_id,
        COUNT(*) FILTER (WHERE naive_included)            AS naive_pay_count_7d,
        COALESCE(SUM(amount) FILTER (WHERE naive_included), 0) AS naive_pay_amount_7d,
        MAX(event_time) FILTER (WHERE naive_included)     AS naive_last_pay_time
    FROM payment_lineage
    GROUP BY sample_id
)
SELECT
    s.sample_id,
    s.user_id,
    s.prediction_time,
    s.observation_cutoff,
    s.scenario,
    COALESCE(c.pay_count_7d, 0)                                      AS pay_count_7d,
    COALESCE(c.pay_amount_7d, 0)                                     AS pay_amount_7d,
    c.last_pay_time,
    COALESCE(n.naive_pay_count_7d, 0)                                AS naive_pay_count_7d,
    COALESCE(n.naive_pay_amount_7d, 0)                               AS naive_pay_amount_7d,
    n.naive_last_pay_time
FROM samples_src s
LEFT JOIN correct_agg c USING (sample_id)
LEFT JOIN naive_agg   n USING (sample_id)
"""

# ---------------------------------------------------------------------------
# 3. ORDER LINEAGE (for labels)
# ---------------------------------------------------------------------------
ORDER_LINEAGE_SQL = """
CREATE OR REPLACE TABLE order_lineage AS
WITH joined AS (
    SELECT
        s.sample_id,
        s.user_id,
        s.prediction_time,
        s.observation_cutoff,
        s.prediction_time + INTERVAL 7 DAY AS label_end,
        o.event_id,
        o.version,
        o.event_time,
        o.ingest_time,
        o.status,
        MAX(o.version) FILTER (WHERE o.ingest_time <= s.observation_cutoff)
            OVER (PARTITION BY s.sample_id, o.event_id) AS latest_visible_version,
        MAX(o.version) OVER (PARTITION BY s.sample_id, o.event_id) AS latest_overall_version
    FROM samples_src s
    JOIN orders_src o ON o.user_id = s.user_id
),
classified AS (
    SELECT
        *,
        (version = latest_visible_version) AS is_latest_visible,
        (ingest_time <= observation_cutoff) AS is_available,
        (event_time >= prediction_time)     AS at_or_after_label_start,
        (event_time <  label_end)           AS before_label_end,
        (status = 'PLACED')                 AS is_placed,
        (version = latest_overall_version)  AS is_latest_overall
    FROM joined
)
SELECT
    *,
    CASE
        WHEN NOT is_available           THEN 'NOT_YET_AVAILABLE'
        WHEN NOT is_latest_visible      THEN 'SUPERSEDED_VERSION'
        WHEN NOT at_or_after_label_start THEN 'OUTSIDE_BEFORE_WINDOW'
        WHEN NOT before_label_end       THEN 'OUTSIDE_AFTER_WINDOW'
        WHEN NOT is_placed              THEN 'CANCELED'
        ELSE 'INCLUDED'
    END AS correct_decision,
    CASE
        WHEN NOT is_latest_overall      THEN 'SUPERSEDED_VERSION'
        WHEN NOT at_or_after_label_start THEN 'OUTSIDE_BEFORE_WINDOW'
        WHEN NOT before_label_end       THEN 'OUTSIDE_AFTER_WINDOW'
        WHEN NOT is_placed              THEN 'CANCELED'
        ELSE 'INCLUDED'
    END AS naive_decision,
    (correct_decision = 'INCLUDED') AS correct_included,
    (naive_decision  = 'INCLUDED') AS naive_included
FROM classified
"""

# ---------------------------------------------------------------------------
# 4. LABELS + FINAL SAMPLE TABLE
# ---------------------------------------------------------------------------
SAMPLES_SQL = """
CREATE OR REPLACE TABLE samples AS
WITH label_agg AS (
    SELECT
        sample_id,
        -- correct label: only meaningful when the window is fully observed
        COALESCE(BOOL_OR(correct_included), FALSE) AS has_order_correct,
        -- naive label: join everything regardless of maturity / availability
        COALESCE(BOOL_OR(naive_included), FALSE)   AS has_order_naive
    FROM order_lineage
    GROUP BY sample_id
)
SELECT
    f.*,
    (f.observation_cutoff >= f.prediction_time + INTERVAL 7 DAY) AS label_mature,
    CASE
        WHEN f.observation_cutoff >= f.prediction_time + INTERVAL 7 DAY
        THEN CAST(l.has_order_correct AS INTEGER)
        ELSE NULL
    END AS label_order_7d,
    CAST(l.has_order_naive AS INTEGER) AS naive_label_order_7d,
    -- divergence flags power the UI leakage comparison
    (f.pay_count_7d       <> f.naive_pay_count_7d
       OR f.pay_amount_7d  <> f.naive_pay_amount_7d
       OR f.last_pay_time IS DISTINCT FROM f.naive_last_pay_time) AS feature_leakage,
    (CASE
        WHEN f.observation_cutoff >= f.prediction_time + INTERVAL 7 DAY
        THEN CAST(l.has_order_correct AS INTEGER) ELSE NULL END
     IS DISTINCT FROM CAST(l.has_order_naive AS INTEGER)) AS label_leakage
FROM features f
LEFT JOIN label_agg l USING (sample_id)
"""

EXPORT_QUERIES = {
    config.SAMPLES_PARQUET: "samples",
    config.PAYMENT_LINEAGE_PARQUET: "payment_lineage",
    config.ORDER_LINEAGE_PARQUET: "order_lineage",
}


def run(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    con.execute(PAYMENT_LINEAGE_SQL)
    con.execute(FEATURES_SQL)
    con.execute(ORDER_LINEAGE_SQL)
    con.execute(SAMPLES_SQL)

    config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for path, table in EXPORT_QUERIES.items():
        con.execute(
            f"COPY {table} TO '{path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return counts


def main() -> None:
    con = duckdb.connect(str(config.DB_PATH))
    try:
        counts = run(con)
    finally:
        con.close()
    for table, n in counts.items():
        print(f"{table}: {n} rows -> parquet exported under {config.EXPORT_DIR}")


if __name__ == "__main__":
    main()
