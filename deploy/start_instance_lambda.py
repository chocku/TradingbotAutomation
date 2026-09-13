"""
Source for the separate `qqq-trader-start-instance` Lambda function.

This is NOT part of the trading bot's own runtime — it's a tiny, standalone
Lambda deployed independently (see deploy_lambda.py), triggered by the
EventBridge schedule (see update_schedule.py). It starts the EC2 instance
and, once reachable via SSM, explicitly triggers `qqq-trader.service` on it.

Deliberately explicit: the EC2 instance's boot-triggered auto-run is
disabled (`systemctl disable qqq-trader.service`) precisely so that a plain
instance start — for a code deploy, a diagnostic check, anything other than
this Lambda's own invocation — never fires a trade. This Lambda's SendCommand
call is the ONLY thing that starts a trading run.

Two independent, deliberately separate timing knobs:
  - EventBridge's schedule (update_schedule.py) controls when the instance
    *boots* — set early, so boot-time variability is never on the critical
    path for the trade itself.
  - TRADE_TIME_ET below controls when the trading logic actually *starts* —
    the instance boots and waits, idle, until this wall-clock time, so the
    signal is computed off the most recently closed 5-minute bar rather than
    whatever the price happened to be right after boot.
Change either one by editing its constant and redeploying (this file via
deploy_lambda.py, the schedule via update_schedule.py) — never by editing a
live Lambda environment variable or the schedule directly in the console,
so git always reflects what's actually running.
"""
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3

INSTANCE_ID          = os.environ.get("INSTANCE_ID", "i-0f5546297f99489aa")
SSM_READY_TIMEOUT_S  = int(os.environ.get("SSM_READY_TIMEOUT_S", "150"))
TRADE_TIME_ET        = "15:56"  # HH:MM, 24h, America/New_York

_ET = ZoneInfo("America/New_York")


def _seconds_until_trade_time() -> float:
    now = datetime.now(_ET)
    hour, minute = (int(p) for p in TRADE_TIME_ET.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (target - now).total_seconds()


def handler(event, context):
    ec2 = boto3.client("ec2")
    ssm = boto3.client("ssm")

    ec2.start_instances(InstanceIds=[INSTANCE_ID])

    deadline = time.time() + SSM_READY_TIMEOUT_S
    registered = False
    while time.time() < deadline:
        resp = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [INSTANCE_ID]}]
        )
        info = resp["InstanceInformationList"]
        if info and info[0]["PingStatus"] == "Online":
            registered = True
            break
        time.sleep(5)

    if not registered:
        return {"statusCode": 500, "body": f"{INSTANCE_ID} never became reachable via SSM"}

    # Instance is ready, but the trade itself waits for TRADE_TIME_ET
    # regardless of how fast or slow the boot was.
    wait_s = _seconds_until_trade_time()
    if wait_s > 0:
        time.sleep(wait_s)

    send_resp = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["systemctl start qqq-trader.service"]},
    )
    return {
        "statusCode": 200,
        "body": (
            f"Triggered qqq-trader.service on {INSTANCE_ID} at ~{TRADE_TIME_ET} ET, "
            f"command {send_resp['Command']['CommandId']}"
        ),
    }
