"""EC2 boot entry point — runs the pipeline once, backs up data, then stops the instance.

Invoked directly on boot (systemd unit / user-data), replacing main.py's own
scheduler loop. EventBridge owns the timing; this script owns one run.
"""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("ec2_entrypoint")


def main() -> None:
    from utils.secrets import load_env_from_secrets
    load_env_from_secrets()

    from main import run_pipeline
    # A one-shot dry-run can be forced two ways: DRY_RUN=true for a manual
    # SSM-invoked test, or an S3 marker for the normal boot-triggered path —
    # the marker avoids racing the boot trigger's own fast self-stop, since
    # it rides along with the real trigger instead of competing with it.
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    if not dry_run:
        try:
            from utils.s3_utils import consume_force_dry_run_marker
            dry_run = consume_force_dry_run_marker()
            if dry_run:
                log.info("Forced dry-run via S3 marker for this boot")
        except Exception as e:
            log.warning("Could not check dry-run marker: %s", e)
    try:
        run_pipeline(dry_run=dry_run)
    except Exception:
        log.exception("Pipeline run crashed")

    from utils.s3_utils import backup_parquet_to_s3
    try:
        backup_parquet_to_s3()
    except Exception as e:
        log.error("Parquet backup step failed: %s", e)

    from utils.instance import self_stop
    self_stop()


if __name__ == "__main__":
    main()
