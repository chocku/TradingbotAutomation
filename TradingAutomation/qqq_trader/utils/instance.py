"""EC2 self-management — used only when running on the EC2 instance itself."""
import logging
import urllib.request

import boto3

log = logging.getLogger(__name__)

_METADATA_BASE = "http://169.254.169.254/latest"


def _get_instance_id() -> str:
    token_req = urllib.request.Request(
        f"{_METADATA_BASE}/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
    )
    token = urllib.request.urlopen(token_req, timeout=2).read().decode()
    id_req = urllib.request.Request(
        f"{_METADATA_BASE}/meta-data/instance-id",
        headers={"X-aws-ec2-metadata-token": token},
    )
    return urllib.request.urlopen(id_req, timeout=2).read().decode()


def self_stop() -> None:
    """Stop (not terminate) this instance so billing stops until the next scheduled run."""
    try:
        instance_id = _get_instance_id()
        ec2 = boto3.client("ec2")
        ec2.stop_instances(InstanceIds=[instance_id])
        log.info("Self-stop requested for %s", instance_id)
    except Exception as e:
        log.error("Self-stop failed — instance will keep running until stopped manually: %s", e)
