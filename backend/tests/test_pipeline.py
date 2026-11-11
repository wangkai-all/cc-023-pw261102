"""End-to-end checks of the point-in-time semantics on the crafted scenarios."""
from __future__ import annotations

import duckdb
import pytest

from backend import generate_data, pipeline


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "test.duckdb"
    c = duckdb.connect(str(db))
    generate_data.build(c)
    pipeline.run(c)
    yield c
    c.close()


def _sample(con, sample_id):
    cols = [d[0] for d in con.execute("SELECT * FROM samples LIMIT 0").description]
    row = con.execute(
        "SELECT * FROM samples WHERE sample_id = ?", [sample_id]
    ).fetchone()
    return dict(zip(cols, row))


def _decisions(con, table, sample_id):
    return con.execute(
        f"""
        SELECT event_id, version, correct_decision, naive_decision
        FROM {table} WHERE sample_id = ? ORDER BY event_id, version
        """,
        [sample_id],
    ).fetchall()


def test_baseline_sample_s0(con):
    s = _sample(con, "S0")
    assert (s["pay_count_7d"], s["pay_amount_7d"]) == (2, 250.0)
    assert s["label_order_7d"] == 1 and s["label_mature"]
    assert not s["feature_leakage"] and not s["label_leakage"]


def test_late_payment_s1_is_not_backfilled(con):
    # P1-2 has business time in window but arrives after prediction_time.
    s = _sample(con, "S1")
    assert (s["pay_count_7d"], s["pay_amount_7d"]) == (1, 120.0)
    # naive join leaks the late payment
    assert (s["naive_pay_count_7d"], s["naive_pay_amount_7d"]) == (2, 200.0)
    assert s["feature_leakage"]
    d = {r[:2]: r[2] for r in _decisions(con, "payment_lineage", "S1")}
    assert d[("P1-2", 1)] == "NOT_YET_AVAILABLE"


def test_later_revocation_s2_does_not_change_history(con):
    # v2 (REVOKED) is not visible at prediction_time, so v1 still counts.
    s = _sample(con, "S2")
    assert (s["pay_count_7d"], s["pay_amount_7d"]) == (1, 200.0)
    # naive sees the globally-latest revocation and drops the payment
    assert s["naive_pay_count_7d"] == 0
    d = {r[:2]: r[2:] for r in _decisions(con, "payment_lineage", "S2")}
    assert d[("P2-1", 1)] == ("INCLUDED", "SUPERSEDED_VERSION")
    assert d[("P2-1", 2)] == ("NOT_YET_AVAILABLE", "REVOKED")


def test_window_boundaries_s3_half_open(con):
    s = _sample(con, "S3")
    # only the exact-left-boundary payment counts; right boundary and the
    # timely-revoked payment are excluded
    assert (s["pay_count_7d"], s["pay_amount_7d"]) == (1, 50.0)
    d = {r[:2]: r[2] for r in _decisions(con, "payment_lineage", "S3")}
    assert d[("P3-1", 1)] == "INCLUDED"            # t-7d exactly
    assert d[("P3-2", 1)] == "NOT_YET_AVAILABLE"   # at t, also not visible
    # visible before t but business time exactly t -> excluded by window only
    assert d[("P3-4", 1)] == "OUTSIDE_AFTER_WINDOW"
    assert d[("P3-3", 2)] == "REVOKED"             # revocation visible before t
    # label window is also half-open: order exactly at t+7d is excluded
    od = {r[:2]: r[2] for r in _decisions(con, "order_lineage", "S3")}
    assert od[("O3-3", 1)] == "OUTSIDE_AFTER_WINDOW"
    assert od[("O3-2", 1)] == "INCLUDED"


def test_immature_label_s4_is_null_not_zero(con):
    s = _sample(con, "S4")
    assert s["label_mature"] is False
    assert s["label_order_7d"] is None
    # naive ignores the observation cutoff and fabricates a label
    assert s["naive_label_order_7d"] == 1
    assert s["label_leakage"]
    d = {r[:2]: r[2] for r in _decisions(con, "order_lineage", "S4")}
    assert d[("O4-1", 1)] == "INCLUDED"
    assert d[("O4-2", 1)] == "NOT_YET_AVAILABLE"


def test_no_payment_history_s5_zero_and_null(con):
    s = _sample(con, "S5")
    assert s["pay_count_7d"] == 0
    assert s["pay_amount_7d"] == 0
    assert s["last_pay_time"] is None
    # the user still exists in the output (inner join must not drop them)
    assert s["label_order_7d"] == 1


def test_backfilled_amount_correction_s6(con):
    s = _sample(con, "S6")
    # at t only v1 (amount 100) exists; v2 (80) arrives later
    assert s["pay_amount_7d"] == 100.0
    assert s["naive_pay_amount_7d"] == 80.0
    # old payment outside the feature window
    d = {r[:2]: r[2] for r in _decisions(con, "payment_lineage", "S6")}
    assert d[("P6-2", 1)] == "OUTSIDE_BEFORE_WINDOW"
    # canceled order: latest visible version at cutoff is CANCELED -> label 0
    assert s["label_order_7d"] == 0
    od = {r[:2]: r[2] for r in _decisions(con, "order_lineage", "S6")}
    assert od[("O6-1", 2)] == "CANCELED"


def test_lineage_covers_every_source_revision(con):
    n_pay = con.execute("SELECT count(*) FROM payments_src").fetchone()[0]
    n_lin = con.execute("SELECT count(*) FROM payment_lineage").fetchone()[0]
    assert n_pay == n_lin  # one sample per user => every revision annotated
