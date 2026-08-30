"""Lambda handler — entry point for AWS Lambda invocation."""
import os
import sys
import logging
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("lambda_handler")

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

# Load secrets from AWS Secrets Manager
try:
    from utils.secrets import load_env_from_secrets
    load_env_from_secrets()
except Exception as e:
    log.error("Failed to load secrets from Secrets Manager: %s", e)
    raise


def lambda_handler(event, context):
    """
    Lambda handler — runs the trading bot when triggered by EventBridge.

    Args:
        event: EventBridge event (unused, but required by Lambda)
        context: Lambda context object (unused)

    Returns:
        dict with statusCode and response
    """
    log.info("=== Lambda invoked ===")

    try:
        # Import after secrets are loaded
        from main import run_pipeline

        # Run the trading pipeline
        run_pipeline()

        return {
            "statusCode": 200,
            "body": "Trading bot completed successfully",
        }

    except Exception as e:
        log.error("Lambda execution failed: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": f"Trading bot failed: {str(e)}",
        }
