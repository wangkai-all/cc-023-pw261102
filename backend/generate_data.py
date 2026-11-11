"""Generate synthetic payment / order events with crafted leakage scenarios.

Every event carries:
  * business event time (event_time)   -- when the business thing happened
  * system availability time (ingest_time) -- when the row became visible
  * event id (event_id) + revision (version) -- revision history per event

All samples share prediction_time = 2026-11-10 00:00:00 so the windows are
easy to reason about:
  feature window = [2026-11-03 00:00, 2026-11-10 00:00)
  label window   = [2026-11-10 00:00, 2026-11-17 00:00)
"""
from __future__ import annotations

import duckdb

from . import config

PREDICTION_TIME = "2026-11-10 00:00:00"

# ---------------------------------------------------------------------------
# Samples: one user + prediction_time + declared observation cutoff
# ---------------------------------------------------------------------------
SAMPLES = [
    # sample_id, user_id, prediction_time, observation_cutoff, scenario
    ("S0", "U0", PREDICTION_TIME, "2026-11-20 00:00:00",
     "基线：正常成熟样本，两条窗口内支付、标签窗口内有订单"),
    ("S1", "U1", PREDICTION_TIME, "2026-11-20 00:00:00",
     "晚到支付：窗口内的支付在预测时刻之后才可用，不能倒灌进特征"),
    ("S2", "U2", PREDICTION_TIME, "2026-11-20 00:00:00",
     "后来撤销：支付先计入、后被 REVOKED 修订，撤销版本预测时刻不可见"),
    ("S3", "U3", PREDICTION_TIME, "2026-11-20 00:00:00",
     "窗口边界：左边界(含)与右边界(不含)事件；预测时刻同刻支付一律排除"),
    ("S4", "U4", PREDICTION_TIME, "2026-11-13 00:00:00",
     "未成熟标签：观察截止只覆盖到 t+3d，标签留空而非当作未下单"),
    ("S5", "U5", PREDICTION_TIME, "2026-11-20 00:00:00",
     "无支付历史：次数/金额为 0，最近支付时间为空"),
    ("S6", "U6", PREDICTION_TIME, "2026-11-20 00:00:00",
     "后来补录修订：预测后金额被修正；以及标签窗口订单晚到"),
]

# ---------------------------------------------------------------------------
# Payment events
# (event_id, user_id, version, event_time, ingest_time, status, amount)
# ---------------------------------------------------------------------------
PAYMENTS = [
    # --- U0: two ordinary payments inside the feature window ---
    ("P0-1", "U0", 1, "2026-11-05 10:00:00", "2026-11-05 10:02:00", "ACTIVE", 100.0),
    ("P0-2", "U0", 1, "2026-11-08 14:00:00", "2026-11-08 14:03:00", "ACTIVE", 150.0),

    # --- U1: one on-time payment + one late payment (business time in window,
    #     but ingested AFTER prediction_time) ---
    ("P1-1", "U1", 1, "2026-11-04 09:00:00", "2026-11-04 09:01:00", "ACTIVE", 120.0),
    ("P1-2", "U1", 1, "2026-11-07 18:00:00", "2026-11-10 08:00:00", "ACTIVE", 80.0),

    # --- U2: payment visible at t, but revoked by a revision that is itself
    #     not yet visible at t (ingest after prediction_time) ---
    ("P2-1", "U2", 1, "2026-11-06 12:00:00", "2026-11-06 12:05:00", "ACTIVE", 200.0),
    ("P2-1", "U2", 2, "2026-11-06 12:00:00", "2026-11-11 09:00:00", "REVOKED", 200.0),

    # --- U3: boundary events.
    #     P3-1 sits exactly on the LEFT boundary (included).
    #     P3-2 sits exactly on prediction_time (right boundary, excluded).
    #     P3-4 is already VISIBLE before t but its business time is exactly t:
    #       it must be excluded purely by the right-exclusive window, proving
    #       the boundary rule is independent of data availability.
    #     P3-3 was revoked before t, so the revocation IS visible at t. ---
    ("P3-1", "U3", 1, "2026-11-03 00:00:00", "2026-11-03 00:01:00", "ACTIVE", 50.0),
    ("P3-2", "U3", 1, "2026-11-10 00:00:00", "2026-11-10 00:00:30", "ACTIVE", 90.0),
    ("P3-4", "U3", 1, "2026-11-10 00:00:00", "2026-11-09 23:59:00", "ACTIVE", 110.0),
    ("P3-3", "U3", 1, "2026-11-05 08:00:00", "2026-11-05 08:02:00", "ACTIVE", 70.0),
    ("P3-3", "U3", 2, "2026-11-05 08:00:00", "2026-11-09 10:00:00", "REVOKED", 70.0),

    # --- U4: ordinary in-window payment; label scenario is in orders below ---
    ("P4-1", "U4", 1, "2026-11-06 16:00:00", "2026-11-06 16:02:00", "ACTIVE", 60.0),

    # U5 intentionally has NO payments.

    # --- U6: payment whose amount is corrected AFTER t; at t only v1 exists.
    #     Also one payment before the feature window (outside, excluded). ---
    ("P6-1", "U6", 1, "2026-11-07 11:00:00", "2026-11-07 11:02:00", "ACTIVE", 100.0),
    ("P6-1", "U6", 2, "2026-11-07 11:00:00", "2026-11-12 10:00:00", "ACTIVE", 80.0),
    ("P6-2", "U6", 1, "2026-11-01 10:00:00", "2026-11-01 10:01:00", "ACTIVE", 300.0),
]

# ---------------------------------------------------------------------------
# Order events (labels)
# (event_id, user_id, version, event_time, ingest_time, status)
# ---------------------------------------------------------------------------
ORDERS = [
    # --- U0: order inside label window, visible well before cutoff ---
    ("O0-1", "U0", 1, "2026-11-12 10:00:00", "2026-11-12 10:01:00", "PLACED"),

    # --- U1: order inside label window ---
    ("O1-1", "U1", 1, "2026-11-15 20:00:00", "2026-11-15 20:02:00", "PLACED"),

    # --- U2: order inside label window ---
    ("O2-1", "U2", 1, "2026-11-11 09:30:00", "2026-11-11 09:35:00", "PLACED"),

    # --- U3: one order inside the label window; one order before it;
    #     one order exactly on the label window's right boundary (excluded). ---
    ("O3-1", "U3", 1, "2026-11-08 12:00:00", "2026-11-08 12:02:00", "PLACED"),
    ("O3-2", "U3", 1, "2026-11-13 15:00:00", "2026-11-13 15:02:00", "PLACED"),
    ("O3-3", "U3", 1, "2026-11-17 00:00:00", "2026-11-17 00:01:00", "PLACED"),

    # --- U4: label window is only partially observable.
    #     O4-1 is inside [t, cutoff) and visible -> evidence of an order.
    #     O4-2 sits inside the label window but is ingested AFTER cutoff:
    #     the correct label is UNMATURE (NULL), not 0.
    #     A naive join over all history sees both orders and still returns 1. ---
    ("O4-1", "U4", 1, "2026-11-11 10:00:00", "2026-11-11 10:02:00", "PLACED"),
    ("O4-2", "U4", 1, "2026-11-15 10:00:00", "2026-11-16 10:00:00", "PLACED"),

    # --- U5: order inside label window; feature side stays empty ---
    ("O5-1", "U5", 1, "2026-11-14 13:00:00", "2026-11-14 13:01:00", "PLACED"),

    # --- U6: label-window order; v2 (canceled) arrives before cutoff, so the
    #     latest visible version at cutoff is CANCELED and must not count.
    #     A naive join that ignores status would count it. ---
    ("O6-1", "U6", 1, "2026-11-12 09:00:00", "2026-11-12 09:01:00", "PLACED"),
    ("O6-1", "U6", 2, "2026-11-12 09:00:00", "2026-11-13 18:00:00", "CANCELED"),
]


def build(con: duckdb.DuckDBPyConnection) -> None:
    """(Re)create source tables and insert the crafted rows."""
    con.execute("DROP TABLE IF EXISTS samples_src")
    con.execute("DROP TABLE IF EXISTS payments_src")
    con.execute("DROP TABLE IF EXISTS orders_src")

    con.execute("""
        CREATE TABLE samples_src (
            sample_id           VARCHAR,
            user_id             VARCHAR,
            prediction_time     TIMESTAMP,
            observation_cutoff  TIMESTAMP,
            scenario            VARCHAR
        )
    """)
    con.executemany(
        "INSERT INTO samples_src VALUES (?, ?, ?, ?, ?)", SAMPLES
    )

    con.execute("""
        CREATE TABLE payments_src (
            event_id     VARCHAR,
            user_id      VARCHAR,
            version      INTEGER,
            event_time   TIMESTAMP,
            ingest_time  TIMESTAMP,
            status       VARCHAR,
            amount       DOUBLE
        )
    """)
    con.executemany(
        "INSERT INTO payments_src VALUES (?, ?, ?, ?, ?, ?, ?)", PAYMENTS
    )

    con.execute("""
        CREATE TABLE orders_src (
            event_id     VARCHAR,
            user_id      VARCHAR,
            version      INTEGER,
            event_time   TIMESTAMP,
            ingest_time  TIMESTAMP,
            status       VARCHAR
        )
    """)
    con.executemany(
        "INSERT INTO orders_src VALUES (?, ?, ?, ?, ?, ?)", ORDERS
    )


def main() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    con = duckdb.connect(str(config.DB_PATH))
    try:
        build(con)
        n_s = con.execute("SELECT count(*) FROM samples_src").fetchone()[0]
        n_p = con.execute("SELECT count(*) FROM payments_src").fetchone()[0]
        n_o = con.execute("SELECT count(*) FROM orders_src").fetchone()[0]
        print(f"loaded {n_s} samples, {n_p} payment rows, {n_o} order rows "
              f"-> {config.DB_PATH}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
