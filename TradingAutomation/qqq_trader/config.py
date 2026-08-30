from pathlib import Path
import os

BASE_DIR = Path(__file__).parent

COMPUTE_TIME_ET      = "15:55"
TIMEZONE             = "America/New_York"
SELL_FILL_TIMEOUT_S  = 30
MIN_SHARE_THRESHOLD  = 1
FRACTIONAL_SHARES    = False
ORDER_TYPE           = "moc"
DATA_RETRY_COUNT     = 3
DATA_RETRY_DELAY_SEC = 5
PARQUET_QQQ          = str(BASE_DIR.parent / "data" / "QQQ_daily_full.parquet")
PARQUET_VIX          = str(BASE_DIR.parent / "data" / "VIX_daily.parquet")
LOG_PATH             = str(BASE_DIR / "logs" / "trades.jsonl")

# AWS Configuration (for Lambda deployment)
AWS_REGION           = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET            = os.environ.get("S3_BUCKET", "qqq-trading-logs")
S3_LOG_KEY           = "trades.jsonl"
S3_DASHBOARD_KEY     = "dashboard.html"
USE_S3               = os.environ.get("USE_S3", "false").lower() == "true"
