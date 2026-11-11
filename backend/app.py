"""FastAPI preview service.

Serves the computed samples + per-record lineage out of the DuckDB file.
Also serves the built React bundle (frontend/dist) when present so the whole
app can run from a single port; in dev, Vite proxies /api to this service.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config

app = FastAPI(title="Point-in-time feature preview", version="1.0.0")

# Vite dev server runs on a different origin; allow it in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Human-readable explanations of every lineage decision code.
REASON_TEXT: dict[str, dict[str, str]] = {
    "payment": {
        "INCLUDED": "纳入：预测时刻可见的最新版本，且业务时间落在 [t-7d, t) 窗口内",
        "NOT_YET_AVAILABLE": "排除：系统可用时间晚于预测时刻（晚到数据，禁止倒灌）",
        "SUPERSEDED_VERSION": "排除：同一事件已有预测时刻可见的更新修订版本",
        "REVOKED": "排除：预测时刻可见的最新版本状态为 REVOKED（已撤销）",
        "OUTSIDE_BEFORE_WINDOW": "排除：业务时间早于特征窗口左边界 t-7d",
        "OUTSIDE_AFTER_WINDOW": "排除：业务时间达到或晚于预测时刻 t（右边界不含）",
    },
    "order": {
        "INCLUDED": "纳入：观察截止前可见的最新 PLACED 版本，业务时间在标签窗口 [t, t+7d) 内",
        "NOT_YET_AVAILABLE": "排除：观察截止时该版本尚不可见（晚到订单）",
        "SUPERSEDED_VERSION": "排除：同一订单已有观察截止前可见的更新修订版本",
        "CANCELED": "排除：观察截止前可见的最新版本状态为 CANCELED（已取消，不计下单）",
        "OUTSIDE_BEFORE_WINDOW": "排除：业务时间早于标签窗口左边界 t",
        "OUTSIDE_AFTER_WINDOW": "排除：业务时间达到或晚于 t+7d（标签窗口右边界不含）",
    },
}


def _get_con() -> duckdb.DuckDBPyConnection:
    if not config.DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="Pipeline has not been run yet. Run `python -m backend.generate_data` "
                   "and `python -m backend.pipeline` first.",
        )
    # read_only=True requires the file to be opened by a single process;
    # the pipeline writes with a separate short-lived connection.
    return duckdb.connect(str(config.DB_PATH), read_only=True)


def _rows(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    con = _get_con()
    try:
        cur = con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, _jsonable(row))) for row in cur.fetchall()]
    finally:
        con.close()


def _jsonable(row: tuple) -> list[Any]:
    out: list[Any] = []
    for v in row:
        if isinstance(v, datetime):
            out.append(v.isoformat(sep=" "))
        else:
            out.append(v)
    return out


SAMPLE_COLS = """
    sample_id, user_id, prediction_time, observation_cutoff, scenario,
    pay_count_7d, pay_amount_7d, last_pay_time,
    naive_pay_count_7d, naive_pay_amount_7d, naive_last_pay_time,
    label_mature, label_order_7d, naive_label_order_7d,
    feature_leakage, label_leakage
"""


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/samples")
def list_samples() -> list[dict[str, Any]]:
    return _rows(f"SELECT {SAMPLE_COLS} FROM samples ORDER BY sample_id")


@app.get("/api/samples/{sample_id}")
def get_sample(sample_id: str) -> dict[str, Any]:
    sample = _rows(
        f"SELECT {SAMPLE_COLS} FROM samples WHERE sample_id = ?", [sample_id]
    )
    if not sample:
        raise HTTPException(status_code=404, detail=f"sample {sample_id} not found")

    payments = _rows(
        """
        SELECT event_id, version, event_time, ingest_time, status, amount,
               correct_decision, naive_decision, correct_included, naive_included
        FROM payment_lineage
        WHERE sample_id = ?
        ORDER BY event_id, version
        """,
        [sample_id],
    )
    orders = _rows(
        """
        SELECT event_id, version, event_time, ingest_time, status,
               correct_decision, naive_decision, correct_included, naive_included
        FROM order_lineage
        WHERE sample_id = ?
        ORDER BY event_id, version
        """,
        [sample_id],
    )
    return {
        "sample": sample[0],
        "payment_lineage": payments,
        "order_lineage": orders,
        "reasons": REASON_TEXT,
    }


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "window_days": config.WINDOW_DAYS,
        "reasons": REASON_TEXT,
        "parquet_dir": str(config.EXPORT_DIR),
    }


# ---------------------------------------------------------------------------
# Static frontend (production build). Mounted last so /api routes win.
# ---------------------------------------------------------------------------
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_DIST / "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:  # pragma: no cover - thin glue
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        index = _DIST / "index.html"
        return FileResponse(str(index))
