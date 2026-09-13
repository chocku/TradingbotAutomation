"""
Pull the package deploy_to_ec2.py just uploaded onto the EC2 instance,
confirm the exact commit that's now running there, and validate it with a
forced dry-run — without ever risking a real trade.

Starts the instance (safe: boot-triggered auto-run is disabled, so this
alone does nothing on its own), deploys the code, forces one dry-run via the
S3 marker, explicitly triggers the service, and reports what changed in S3.
Leaves the instance stopped either way.

Usage:
    python verify_on_ec2.py
"""
import subprocess
import sys
import time
from pathlib import Path

import boto3

REPO_ROOT   = Path(__file__).resolve().parent.parent
INSTANCE_ID = "i-0f5546297f99489aa"
BUCKET      = "qqq-trading-logs-chock"
REGION      = "us-east-1"

ec2 = boto3.client("ec2", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)
s3  = boto3.client("s3", region_name=REGION)


def _expected_commit() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True)
    return result.stdout.strip()


def _run_cmd(commands: list[str], timeout: int = 180, label: str = "") -> tuple[str, str]:
    resp = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands},
        TimeoutSeconds=timeout,
    )
    command_id = resp["Command"]["CommandId"]
    for _ in range(timeout // 2):
        time.sleep(2)
        inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=INSTANCE_ID)
        if inv["Status"] not in ("Pending", "InProgress", "Delayed"):
            print(f"=== {label} -> {inv['Status']} ===")
            return inv["StandardOutputContent"], inv["StandardErrorContent"]
    print(f"{label}: timed out waiting for completion")
    return "", ""


def main() -> None:
    expected_commit = _expected_commit()
    print(f"Expecting commit {expected_commit[:8]} to end up running on the instance")

    print("Starting instance (safe — boot alone triggers nothing)...")
    ec2.start_instances(InstanceIds=[INSTANCE_ID])
    ec2.get_waiter("instance_running").wait(InstanceIds=[INSTANCE_ID])

    for _ in range(30):
        info = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [INSTANCE_ID]}])
        if info["InstanceInformationList"] and info["InstanceInformationList"][0]["PingStatus"] == "Online":
            break
        time.sleep(5)
    else:
        sys.exit("Instance never became reachable via SSM")

    stdout, _ = _run_cmd([
        "aws s3 cp s3://qqq-trading-logs-chock/deploy/app.zip /tmp/app.zip",
        "sudo unzip -o /tmp/app.zip -d /opt/qqq-trader",
        "cat /opt/qqq-trader/DEPLOYED_COMMIT.txt",
    ], label="pull code")
    deployed_commit = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    if deployed_commit != expected_commit:
        print(f"WARNING: instance is running {deployed_commit[:8] or '(unknown)'}, expected {expected_commit[:8]}")
    else:
        print(f"Confirmed: instance is now running commit {deployed_commit[:8]}")

    s3.put_object(Bucket=BUCKET, Key="control/force_dry_run.flag", Body=b"")
    print("Dry-run marker planted — next run will not submit a real order")

    before = {o["Key"]: o["LastModified"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])}

    _run_cmd(["sudo systemctl start qqq-trader.service"], timeout=150, label="run (dry-run, marker-forced)")
    _run_cmd(["sudo tail -n 80 /var/log/qqq-trader.log"], label="pipeline log")

    after = {o["Key"]: o["LastModified"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])}
    print("\n=== S3 objects that changed ===")
    changed = [k for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
    for key in changed:
        print(f"  {key}")
    if not changed:
        print("  (none — check the pipeline log above for what happened)")

    print("\nStopping instance...")
    ec2.stop_instances(InstanceIds=[INSTANCE_ID])
    ec2.get_waiter("instance_stopped").wait(InstanceIds=[INSTANCE_ID])
    print("Done. Instance stopped. If this looks right, the schedule will use this code on the next trading day.")


if __name__ == "__main__":
    main()
