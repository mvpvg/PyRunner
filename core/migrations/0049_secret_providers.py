# External Secret Providers, Stage 1 — the backend seam.
#
# Adds the SecretProvider profile table plus three fields on Secret (source /
# provider FK / external_ref) that make a secret's VALUE pluggable: source="local"
# (the default) stays byte-for-byte on today's Fernet path, source="external"
# resolves live via a provider adapter. No data migration is needed — every
# existing row defaults to source="local" and encrypted_value is only relaxed to
# blank=True (a widening, safe for all stored rows). Reversible.

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0048_databases"),
    ]

    operations = [
        migrations.CreateModel(
            name="SecretProvider",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "provider_type",
                    models.CharField(
                        help_text="Backend adapter key (validated against the registry)",
                        max_length=30,
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Display name, e.g. 'Prod Vault' or 'AWS us-east-1'",
                        max_length=100,
                        unique=True,
                    ),
                ),
                (
                    "config",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Non-secret adapter settings (base_url, region, mount, …)",
                    ),
                ),
                (
                    "credentials_encrypted",
                    models.TextField(
                        blank=True,
                        help_text="Fernet-encrypted JSON of the adapter's credential fields",
                    ),
                ),
                (
                    "cache_ttl",
                    models.PositiveIntegerField(
                        default=300,
                        help_text="Seconds to cache a fetched secret path in-process (0 = no caching)",
                    ),
                ),
                (
                    "on_error",
                    models.CharField(
                        choices=[
                            ("fail", "Fail the run"),
                            ("use_stale", "Serve last cached value"),
                        ],
                        default="fail",
                        help_text="What to do when a live fetch fails (fail the run, or serve a stale cached value if one exists)",
                        max_length=20,
                    ),
                ),
                (
                    "last_tested_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When this provider's connection last tested successfully",
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "secret provider",
                "verbose_name_plural": "secret providers",
                "db_table": "secret_providers",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="secret",
            name="external_ref",
            field=models.CharField(
                blank=True,
                help_text="Reference to the value within the provider (external rows only).",
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name="secret",
            name="source",
            field=models.CharField(
                choices=[("local", "Stored value"), ("external", "External provider")],
                default="local",
                help_text="Where the value comes from: a locally-stored encrypted value, or a live fetch from an external secrets provider.",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="secret",
            name="encrypted_value",
            field=models.TextField(
                blank=True,
                help_text="Fernet-encrypted secret value (blank for external rows)",
            ),
        ),
        migrations.AddField(
            model_name="secret",
            name="provider",
            field=models.ForeignKey(
                blank=True,
                help_text="External provider profile (external rows only).",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="secrets",
                to="core.secretprovider",
            ),
        ),
    ]
