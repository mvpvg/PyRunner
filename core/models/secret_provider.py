"""
External secret-provider profiles.

One row per configured external secrets backend (a HashiCorp Vault / OpenBao
server, an AWS Secrets Manager region, an Infisical project, …). A ``Secret``
row with ``source="external"`` points at one of these via FK and stores a
``external_ref`` (e.g. ``kv/myapp#API_KEY``); its value is fetched live at run
time by the matching adapter in ``core.services.secret_backends``.

``provider_type`` is a plain CharField validated against the backend REGISTRY at
the form layer — deliberately NOT ``TextChoices`` (unlike ``AIProvider``): choices
on the model would force a model edit + migration per new adapter, defeating the
"one adapter file + one register call" extensibility goal. The non-secret
settings live in ``config`` (JSON); the credential fields are Fernet-encrypted as
a JSON blob in ``credentials_encrypted`` (same encryption stance as ``Secret``).
"""

import json
import uuid

from django.db import models


class SecretProvider(models.Model):
    """An instance-global connection profile to an external secrets manager."""

    class OnError(models.TextChoices):
        FAIL = "fail", "Fail the run"
        USE_STALE = "use_stale", "Serve last cached value"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Plain CharField, NOT TextChoices — validated against list_backends() at the
    # form layer so a new adapter needs no model edit / migration. See module docstring.
    provider_type = models.CharField(
        max_length=30,
        help_text="Backend adapter key (validated against the registry)",
    )
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Display name, e.g. 'Prod Vault' or 'AWS us-east-1'",
    )
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Non-secret adapter settings (base_url, region, mount, …)",
    )
    credentials_encrypted = models.TextField(
        blank=True,
        help_text="Fernet-encrypted JSON of the adapter's credential fields",
    )
    cache_ttl = models.PositiveIntegerField(
        default=300,
        help_text="Seconds to cache a fetched secret path in-process (0 = no caching)",
    )
    on_error = models.CharField(
        max_length=20,
        choices=OnError.choices,
        default=OnError.FAIL,
        help_text="What to do when a live fetch fails (fail the run, or serve a "
        "stale cached value if one exists)",
    )
    last_tested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this provider's connection last tested successfully",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "secret_providers"
        ordering = ["name"]
        verbose_name = "secret provider"
        verbose_name_plural = "secret providers"

    def __str__(self):
        return f"{self.name} ({self.provider_type})"

    def get_credentials(self) -> dict:
        """Decrypt and return the credential fields as a dict (``{}`` if unset)."""
        if not self.credentials_encrypted:
            return {}
        from core.services import EncryptionService

        return json.loads(EncryptionService.decrypt(self.credentials_encrypted))

    def set_credentials(self, creds: dict) -> None:
        """Fernet-encrypt and store the credential fields (blank blob if empty)."""
        from core.services import EncryptionService

        self.credentials_encrypted = (
            EncryptionService.encrypt(json.dumps(creds)) if creds else ""
        )
