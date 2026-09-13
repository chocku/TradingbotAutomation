"""
Package the current git HEAD and upload it to S3 for the EC2 instance to pull.

Refuses to run unless the working tree is clean AND matches origin/main, so
what gets deployed always corresponds to an exact, traceable, pushed commit
-- never uncommitted or unpushed local changes.

Only application code is packaged here, not the parquet data files: those
live and grow on the instance's own disk from real trading runs, and
shipping a local snapshot over them would silently roll back its history.

Usage:
    python deploy_to_ec2.py
"""
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUCKET    = "qqq-trading-logs-chock"
KEY       = "deploy/app.zip"

INCLUDE_PATHS = [
    "strategy.py",
    "TradingAutomation/qqq_trader",
]
EXCLUDE_DIR_NAMES  = {"__pycache__", "logs"}
EXCLUDE_FILE_NAMES = {"dashboard.html"}
EXCLUDE_SUFFIXES   = {".parquet", ".pyc"}


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout.strip()


def _check_git_clean_and_synced() -> str:
    status = _run(["git", "status", "--porcelain"])
    if status:
        sys.exit(f"Working tree has uncommitted changes — commit first:\n{status}")

    _run(["git", "fetch", "origin", "main"])
    local_sha  = _run(["git", "rev-parse", "HEAD"])
    remote_sha = _run(["git", "rev-parse", "origin/main"])
    if local_sha != remote_sha:
        sys.exit(
            f"Local HEAD ({local_sha[:8]}) differs from origin/main ({remote_sha[:8]}) "
            "— push before deploying."
        )
    return local_sha


def _package(commit_sha: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_root in INCLUDE_PATHS:
            abs_root = REPO_ROOT / rel_root
            if abs_root.is_file():
                zf.write(abs_root, arcname=rel_root)
                continue
            for path in abs_root.rglob("*"):
                if not path.is_file():
                    continue
                if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
                    continue
                if path.name in EXCLUDE_FILE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
                    continue
                zf.write(path, arcname=str(path.relative_to(REPO_ROOT)))
        # Lets verify_on_ec2.py confirm the exact commit actually running there.
        zf.writestr("DEPLOYED_COMMIT.txt", commit_sha + "\n")
    return buf.getvalue()


def main() -> None:
    commit_sha = _check_git_clean_and_synced()
    print(f"Deploying commit {commit_sha[:8]} (clean, matches origin/main)")

    zip_bytes = _package(commit_sha)
    print(f"Package size: {len(zip_bytes) / 1024:.1f} KB")

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=zip_bytes)
    print(f"Uploaded to s3://{BUCKET}/{KEY}")
    print("Next: run verify_on_ec2.py to pull this onto the instance and dry-run test it.")


if __name__ == "__main__":
    main()
