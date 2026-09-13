"""
Source for the separate `qqq-trader-start-instance` Lambda function.

This is NOT part of the trading bot's own runtime — it's a tiny, standalone
Lambda deployed independently (see AWS Lambda console / IaC), triggered by
the EventBridge schedule. It starts the EC2 instance and, once reachable via
SSM, explicitly triggers `qqq-trader.service` on it.

Deliberately explicit: the EC2 instance's boot-triggered auto-run is
disabled (`systemctl disable qqq-trader.service`) precisely so that a plain
instance start — for a code deploy, a diagnostic check, anything other than
this Lambda's own invocation — never fires a trade. This Lambda's SendCommand
call is the ONLY thing that starts a trading run.
"""
import os
import time
import boto3

INSTANCE_ID = os.environ.get("INSTANCE_ID", "i-0f5546297f99489aa")
SSM_READY_TIMEOUT_S = int(os.environ.get("SSM_READY_TIMEOUT_S", "150"))


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

    send_resp = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["systemctl start qqq-trader.service"]},
    )
    return {
        "statusCode": 200,
        "body": f"Triggered qqq-trader.service on {INSTANCE_ID}, command {send_resp['Command']['CommandId']}",
    }
