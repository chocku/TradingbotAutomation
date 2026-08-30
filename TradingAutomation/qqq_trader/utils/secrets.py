"""Retrieve secrets from AWS Secrets Manager (Lambda only)."""
import os
import json
import logging
import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

secrets_client = boto3.client("secretsmanager")


def get_secret(secret_name: str) -> str:
    """Retrieve a secret from AWS Secrets Manager."""
    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        return response.get("SecretString", "")
    except ClientError as e:
        log.error("Failed to retrieve secret %s: %s", secret_name, e)
        raise


def load_env_from_secrets() -> None:
    """Load Alpaca and Gmail credentials from Secrets Manager into env vars."""
    try:
        alpaca_key_secret = os.environ.get("ALPACA_KEY_SECRET", "qqq-trader/alpaca-key")
        alpaca_secret_secret = os.environ.get(
            "ALPACA_SECRET_SECRET", "qqq-trader/alpaca-secret"
        )
        gmail_password_secret = os.environ.get(
            "GMAIL_PASSWORD_SECRET", "qqq-trader/gmail-password"
        )

        os.environ["ALPACA_KEY"] = get_secret(alpaca_key_secret)
        os.environ["ALPACA_SECRET"] = get_secret(alpaca_secret_secret)
        os.environ["GMAIL_APP_PASSWORD"] = get_secret(gmail_password_secret)

        log.info("Secrets loaded from Secrets Manager")
    except Exception as e:
        log.error("Failed to load secrets: %s", e)
        raise
