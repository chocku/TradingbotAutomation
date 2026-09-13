"""
Deploy start_instance_lambda.py (this directory) as the qqq-trader-start-instance
Lambda function's code, straight from git — same discipline as deploy_to_ec2.py:
refuses to run on an unclean or unpushed working tree.

Usage:
    python deploy_lambda.py
"""
import io
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REPO_ROOT     = Path(__file__).resolve().parent.parent
FUNCTION_NAME = "qqq-trader-start-instance"
SOURCE_FILE   = Path(__file__).resolve().parent / "start_instance_lambda.py"

# Must comfortably cover: SSM-ready wait (up to SSM_READY_TIMEOUT_S in the
# Lambda itself) + the wait until TRADE_TIME_ET, which can be several
# minutes if the EventBridge boot trigger fires well before it.
LAMBDA_TIMEOUT_SECONDS = 480


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


def main() -> None:
    commit_sha = _check_git_clean_and_synced()
    print(f"Deploying commit {commit_sha[:8]} (clean, matches origin/main)")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(SOURCE_FILE, arcname="lambda_function.py")
    zip_bytes = buf.getvalue()

    lam = boto3.client("lambda", region_name="us-east-1")
    lam.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=zip_bytes)
    print(f"Updated {FUNCTION_NAME} from {SOURCE_FILE.relative_to(REPO_ROOT)}")

    # The code update above applies asynchronously; retry the config update
    # until Lambda's done processing it.
    for attempt in range(10):
        try:
            lam.update_function_configuration(FunctionName=FUNCTION_NAME, Timeout=LAMBDA_TIMEOUT_SECONDS)
            print(f"Timeout set to {LAMBDA_TIMEOUT_SECONDS}s")
            break
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceConflictException" or attempt == 9:
                raise
            time.sleep(3)


if __name__ == "__main__":
    main()
