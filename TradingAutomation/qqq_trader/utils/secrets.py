"""Load bot credentials and config from a single AWS Secrets Manager secret (Lambda/EC2 only)."""
import os
import json
import logging
import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

secrets_client = boto3.client("secretsmanager")

# Keys copied from the secret into the environment. AWS_ACCESS_KEY_ID /
# AWS_SECRET_ACCESS_KEY are deliberately excluded — the instance's IAM role
# supplies AWS credentials, so static keys are never loaded into the process.
_ENV_KEYS = [
    "ALPACA_KEY", "ALPACA_SECRET", "GMAIL_APP_PASSWORD",
    "AWS_REGION", "S3_BUCKET", "USE_S3",
]


def load_env_from_secrets() -> None:
    """Load credentials/config from the combined Secrets Manager secret into env vars."""
    secret_name = os.environ.get("CREDENTIALS_SECRET_NAME", "qqq-trading-bot/credentials")
    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        data = json.loads(response["SecretString"])
        for key in _ENV_KEYS:
            if key in data:
                os.environ[key] = str(data[key])
        log.info("Secrets loaded from Secrets Manager (%s)", secret_name)
    except ClientError as e:
        log.error("Failed to retrieve secret %s: %s", secret_name, e)
        raise
