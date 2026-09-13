"""
Apply BOOT_TIME_ET below to the qqq-trader-daily-start EventBridge schedule —
this is the *other* timing knob, separate from start_instance_lambda.py's
TRADE_TIME_ET. This one controls when the EC2 instance boots (early, for
safety margin against boot-time variability); TRADE_TIME_ET controls when
the trading logic actually starts once the instance is ready.

Same discipline as the other deploy scripts: refuses to run on an unclean or
unpushed working tree, so the live schedule always matches what's in git.

Usage:
    python update_schedule.py
"""
import subprocess
import sys
from pathlib import Path

import boto3

REPO_ROOT     = Path(__file__).resolve().parent.parent
SCHEDULE_NAME = "qqq-trader-daily-start"
REGION        = "us-east-1"
TIMEZONE      = "America/New_York"

BOOT_TIME_ET = "15:50"  # HH:MM, 24h — must stay comfortably before TRADE_TIME_ET


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
    print(f"Applying commit {commit_sha[:8]} (clean, matches origin/main)")

    hour, minute = (int(p) for p in BOOT_TIME_ET.split(":"))
    cron_expression = f"cron({minute} {hour} ? * MON-FRI *)"

    scheduler = boto3.client("scheduler", region_name=REGION)
    current = scheduler.get_schedule(Name=SCHEDULE_NAME)

    scheduler.update_schedule(
        Name=SCHEDULE_NAME,
        ScheduleExpression=cron_expression,
        ScheduleExpressionTimezone=TIMEZONE,
        FlexibleTimeWindow=current["FlexibleTimeWindow"],
        State=current["State"],
        Target=current["Target"],
    )
    print(f"Schedule '{SCHEDULE_NAME}' now boots the instance at {BOOT_TIME_ET} ET, Mon-Fri "
          f"({cron_expression}), state={current['State']}")


if __name__ == "__main__":
    main()
