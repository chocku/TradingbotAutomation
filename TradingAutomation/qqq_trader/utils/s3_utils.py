"""S3 utilities for Lambda deployment — read/write logs and dashboard to S3."""
import json
import os
import logging
import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

# AWS config
S3_BUCKET = os.environ.get("S3_BUCKET", "qqq-trading-logs-chock")
S3_LOG_KEY = "trades.jsonl"
S3_DASHBOARD_KEY = "dashboard.html"
S3_PERFORMANCE_KEY = "performance_daily.json"

s3_client = boto3.client("s3")


def read_log_from_s3() -> list:
    """Read all trade entries from S3 JSONL file. Returns list of dicts."""
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_LOG_KEY)
        content = response["Body"].read().decode("utf-8")
        trades = []
        for line in content.strip().split("\n"):
            if line.strip():
                trades.append(json.loads(line))
        return trades
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            log.info("Log file does not exist in S3 yet — starting fresh")
            return []
        raise


def append_log_to_s3(entry: dict) -> None:
    """Append a single trade entry to S3 JSONL file."""
    try:
        # Read existing log
        trades = read_log_from_s3()
        # Append new entry
        trades.append(entry)
        # Write back
        content = "\n".join(json.dumps(t) for t in trades)
        s3_client.put_object(
            Bucket=S3_BUCKET, Key=S3_LOG_KEY, Body=content.encode("utf-8")
        )
        log.info("Trade logged to S3: %s", entry.get("ticker", "N/A"))
    except Exception as e:
        log.error("Failed to append log to S3: %s", e)
        raise


def upload_dashboard_to_s3(html_content: str) -> None:
    """Upload dashboard HTML to S3."""
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=S3_DASHBOARD_KEY,
            Body=html_content.encode("utf-8"),
            ContentType="text/html",
        )
        log.info("Dashboard uploaded to S3")
    except Exception as e:
        log.error("Failed to upload dashboard to S3: %s", e)
        raise


def upload_performance_to_s3(data: dict) -> None:
    """Upload the normalized daily performance dataset to S3."""
    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key=S3_PERFORMANCE_KEY,
        Body=json.dumps(data, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def get_dashboard_url() -> str:
    """Return public S3 URL for dashboard."""
    return f"https://{S3_BUCKET}.s3.amazonaws.com/{S3_DASHBOARD_KEY}"
