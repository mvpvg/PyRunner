"""
Plugin registry model.

Tracks installed plugins and their lifecycle state. The DB is the source of
truth for *what should load*; the filesystem (``PLUGINS_DIR``) is the source of
truth for *what code exists*. Only ``status=ACTIVE`` plugins are ever imported
into the running process (see the guarded loader in ``pyrunner/settings.py``).
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import models

from core.plugins import is_valid_plugin_slug


def validate_plugin_slug(value: str) -> None:
    if not is_valid_plugin_slug(value):
        raise ValidationError(
            "Slug must start with a lowercase letter and contain only "
            "lowercase letters, digits, and underscores."
        )


class Plugin(models.Model):
    """A plugin known to PyRunner (its files live on the data volume)."""

    class Status(models.TextChoices):
        INSTALLED = "installed", "Installed"  # files on disk, not loaded
        ACTIVE = "active", "Active"  # loaded into the running process
        DISABLED = "disabled", "Disabled"  # deactivated by an admin (data kept)
        ERRORED = "errored", "Errored"  # failed preflight/boot; quarantined

    class Source(models.TextChoices):
        UPLOAD = "upload", "Upload"
        GIT = "git", "Git"
        BUILTIN = "builtin", "Built-in"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    slug = models.CharField(
        max_length=100,
        unique=True,
        validators=[validate_plugin_slug],
        help_text="Matches the plugin folder name and its app_name.",
    )
    name = models.CharField(max_length=200)
    version = models.CharField(max_length=50, blank=True, default="")

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.INSTALLED,
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.UPLOAD,
    )

    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Last preflight/boot failure (shown in the UI).",
    )
    manifest = models.JSONField(
        default=dict,
        blank=True,
        help_text="Parsed plugin.json contents.",
    )
    checksum = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Checksum of the unpacked folder (integrity check).",
    )

    installed_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "plugins"
        ordering = ["name"]
        verbose_name = "plugin"
        verbose_name_plural = "plugins"

    def __str__(self):
        return f"{self.name} ({self.slug}) [{self.status}]"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    def mark_errored(self, message: str) -> None:
        """Quarantine this plugin so the next boot will not load it."""
        self.status = self.Status.ERRORED
        self.error_message = message or "Plugin failed preflight."
        self.save(update_fields=["status", "error_message", "updated_at"])
