"""Central path configuration for the point-in-time feature pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "pipeline.duckdb"
EXPORT_DIR = DATA_DIR / "parquet"

# Parquet outputs
SAMPLES_PARQUET = EXPORT_DIR / "samples.parquet"
PAYMENT_LINEAGE_PARQUET = EXPORT_DIR / "payment_lineage.parquet"
ORDER_LINEAGE_PARQUET = EXPORT_DIR / "order_lineage.parquet"

WINDOW_DAYS = 7
