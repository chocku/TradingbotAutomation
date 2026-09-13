"""
Package the current git HEAD and upload it to S3 for the EC2 instance to pull.

Refuses to run unless the working tree is clean AND matches origin/main, so
what gets deployed always corresponds to an exact, traceable, pushed commit
-- never uncommitted or unpushed local changes.

Ships an explicit manifest of exactly what runs on the EC2 instance, not
"everything in this folder" — dev tooling (this script included), one-time
utilities, and Replit-era leftovers never make it into the bundle no matter
what else lives alongside them in the repo. Data files (parquet) are never
shipped either: they live and grow on the instance's own disk from real
trading runs, and overwriting them from a local snapshot would silently
roll back its history.

Usage:
    python deploy_to_ec2.py
"""
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
BUCKET    = "qqq-trading-logs-chock"
KEY       = "deploy/app.zip"

# Everything the EC2 instance actually needs to run the pipeline. Anything
# not listed here never ships, regardless of what else exists in the repo.
APP_FILES = [
    "strategy.py",
    "TradingAutomation/qqq_trader/main.py",
    "TradingAutomation/qqq_trader/ec2_entrypoint.py",
    "TradingAutomation/qqq_trader/config.py",
    "TradingAutomation/qqq_trader/dashboard.py",
    "TradingAutomation/qqq_trader/performance.py",
    "TradingAutomation/qqq_trader/requirements.txt",
]
APP_DIRS = [
    "TradingAutomation/qqq_trader/data",
    "TradingAutomation/qqq_trader/orders",
    "TradingAutomation/qqq_trader/signal_engine",
    "TradingAutomation/qqq_trader/utils",
]
EXCLUDE_DIR_NAMES = {"__pycache__"}
EXCLUDE_SUFFIXES  = {".pyc"}


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


def _package(commit_sha: str) -> tuple[bytes, list[str]]:
    manifest: list[str] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in APP_FILES:
            abs_path = REPO_ROOT / rel_path
            if not abs_path.is_file():
                sys.exit(f"Expected app file missing: {rel_path}")
            zf.write(abs_path, arcname=rel_path)
            manifest.append(rel_path)

        for rel_root in APP_DIRS:
            abs_root = REPO_ROOT / rel_root
            if not abs_root.is_dir():
                sys.exit(f"Expected app directory missing: {rel_root}")
            for path in sorted(abs_root.rglob("*")):
                if not path.is_file():
                    continue
                if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
                    continue
                if path.suffix in EXCLUDE_SUFFIXES:
                    continue
                rel = str(path.relative_to(REPO_ROOT))
                zf.write(path, arcname=rel)
                manifest.append(rel)

        # Lets verify_on_ec2.py confirm the exact commit actually running there.
        zf.writestr("DEPLOYED_COMMIT.txt", commit_sha + "\n")

    return buf.getvalue(), manifest


def main() -> None:
    commit_sha = _check_git_clean_and_synced()
    print(f"Deploying commit {commit_sha[:8]} (clean, matches origin/main)")

    zip_bytes, manifest = _package(commit_sha)
    print(f"Package size: {len(zip_bytes) / 1024:.1f} KB — {len(manifest)} files:")
    for rel in manifest:
        print(f"  {rel}")

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=zip_bytes)
    print(f"\nUploaded to s3://{BUCKET}/{KEY}")
    print("Next: run verify_on_ec2.py to pull this onto the instance and dry-run test it.")


if __name__ == "__main__":
    main()
