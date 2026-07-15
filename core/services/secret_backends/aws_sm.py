"""
AWS Secrets Manager adapter (boto3 — already a core dependency).

Credentials are an access key id + secret key; **both blank means the ambient
IAM role** is used (EC2/ECS/EKS instance profile, or the standard boto3 credential
chain) — the common production setup. Region is the only required config.

Reference is ``name-or-arn#json_key``: the part before ``#`` is the secret's name
or ARN; the optional ``#json_key`` picks a key out of a JSON secret. Omit it for a
plaintext secret (the whole string is returned). boto3 is imported lazily so a
boto3 problem never breaks the whole ``secret_backends`` package on import.
"""

import json

from .base import SecretBackend, SecretResolutionError, register


class AWSSecretsManagerBackend(SecretBackend):
    provider_key = "aws_sm"
    label = "AWS Secrets Manager"
    docs_url = "https://docs.aws.amazon.com/secretsmanager/latest/userguide/"

    fields = [
        {
            "name": "region",
            "label": "Region",
            "kind": "config",
            "required": True,
            "placeholder": "us-east-1",
            "help": "AWS region the secrets live in.",
        },
        {
            "name": "access_key_id",
            "label": "Access key ID",
            "kind": "credential",
            "required": False,
            "placeholder": "AKIA…",
            "help": "Leave blank (with the secret key) to use an ambient IAM role.",
        },
        {
            "name": "secret_access_key",
            "label": "Secret access key",
            "kind": "credential",
            "required": False,
            "placeholder": "…",
            "help": "Leave blank (with the access key ID) to use an ambient IAM role.",
        },
    ]
    ref_placeholder = "my-secret-name#json_key"
    ref_help = (
        "Secret name or ARN, optionally '#json_key' to pick a key from a JSON "
        "secret. Omit '#json_key' for a plaintext secret."
    )

    def _client(self, profile):
        import boto3

        config = profile.config or {}
        region = (config.get("region") or "").strip()
        if not region:
            raise SecretResolutionError("AWS region is not configured")
        creds = profile.get_credentials() or {}
        akid = (creds.get("access_key_id") or "").strip()
        secret = (creds.get("secret_access_key") or "").strip()
        if bool(akid) != bool(secret):
            raise SecretResolutionError(
                "Provide both the AWS access key ID and secret key, or neither "
                "(to use an ambient IAM role)"
            )
        kwargs = {"region_name": region}
        if akid and secret:
            kwargs["aws_access_key_id"] = akid
            kwargs["aws_secret_access_key"] = secret
        return boto3.client("secretsmanager", **kwargs)

    def test_connection(self, profile) -> tuple[bool, str]:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            client = self._client(profile)
        except SecretResolutionError as e:
            return False, str(e)
        try:
            client.list_secrets(MaxResults=1)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("AccessDeniedException", "AccessDenied"):
                # Auth succeeded (request was signed and reached IAM) but the
                # policy doesn't allow ListSecrets — GetSecretValue may still work.
                return True, "Credentials accepted (ListSecrets denied; fetches may still work)."
            return False, f"AWS rejected the request ({code or 'ClientError'})."
        except BotoCoreError as e:
            # NoCredentialsError / EndpointConnectionError are BotoCoreError subclasses.
            return False, f"Could not reach AWS Secrets Manager: {e}"
        return True, "AWS Secrets Manager reachable."

    def fetch(self, profile, path: str) -> dict[str, str]:
        from botocore.exceptions import BotoCoreError, ClientError

        client = self._client(profile)
        name = path.strip()
        try:
            resp = client.get_secret_value(SecretId=name)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "ResourceNotFoundException":
                raise SecretResolutionError(
                    f"secret {name!r} not found in AWS Secrets Manager"
                ) from e
            raise SecretResolutionError(
                f"AWS Secrets Manager error ({code or 'ClientError'}): {e}"
            ) from e
        except BotoCoreError as e:
            raise SecretResolutionError(f"AWS Secrets Manager request failed: {e}") from e

        raw = resp.get("SecretString")
        if raw is None:
            raise SecretResolutionError(
                "binary secrets are not supported — store a string/JSON secret"
            )
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
        # Plaintext secret: single value keyed by the secret name (the '#'-less ref
        # resolves it via the single-value fallback).
        return {name: raw}


register(AWSSecretsManagerBackend())
