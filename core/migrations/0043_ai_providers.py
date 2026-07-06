# Generic AI providers: AIProvider profiles replace the single-provider
# claude_* fields on GlobalSettings. The data migration seeds one Anthropic
# provider row per stored credential (subscription token / API key) and points
# active_ai_provider at the one matching the old auth-method selection, so
# existing installs upgrade with zero reconfiguration. Encrypted blobs copy
# verbatim (same Fernet key). Reversible.

import uuid

import django.db.models.deletion
from django.db import migrations, models


def seed_providers(apps, schema_editor):
    GlobalSettings = apps.get_model("core", "GlobalSettings")
    AIProvider = apps.get_model("core", "AIProvider")

    s = GlobalSettings.objects.filter(pk=1).first()
    if s is None:
        return

    active = None
    if s.claude_oauth_token_encrypted:
        sub = AIProvider.objects.create(
            provider_type="anthropic",
            name="Claude subscription",
            auth_method="subscription",
            credential_encrypted=s.claude_oauth_token_encrypted,
            default_model=s.claude_default_model or "",
        )
        if s.claude_auth_method == "subscription":
            sub.last_tested_at = s.claude_last_tested_at
            sub.save(update_fields=["last_tested_at"])
            active = sub

    if s.claude_api_key_encrypted:
        key = AIProvider.objects.create(
            provider_type="anthropic",
            name="Anthropic API key",
            auth_method="api_key",
            credential_encrypted=s.claude_api_key_encrypted,
            default_model=s.claude_default_model or "",
        )
        if s.claude_auth_method == "api_key":
            key.last_tested_at = s.claude_last_tested_at
            key.save(update_fields=["last_tested_at"])
            active = key

    if active is not None:
        s.active_ai_provider = active
        s.save(update_fields=["active_ai_provider"])


def unseed_providers(apps, schema_editor):
    """Copy the Anthropic rows back onto the old GlobalSettings fields."""
    GlobalSettings = apps.get_model("core", "GlobalSettings")
    AIProvider = apps.get_model("core", "AIProvider")

    s = GlobalSettings.objects.filter(pk=1).first()
    if s is None:
        return

    for p in AIProvider.objects.filter(provider_type="anthropic"):
        if p.auth_method == "subscription" and not s.claude_oauth_token_encrypted:
            s.claude_oauth_token_encrypted = p.credential_encrypted
        elif p.auth_method == "api_key" and not s.claude_api_key_encrypted:
            s.claude_api_key_encrypted = p.credential_encrypted

    active = s.active_ai_provider
    if active is not None and active.provider_type == "anthropic":
        s.claude_auth_method = active.auth_method
        s.claude_default_model = active.default_model
        s.claude_last_tested_at = active.last_tested_at
    s.save()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0042_cache_table"),
    ]

    operations = [
        migrations.CreateModel(
            name="AIProvider",
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
                        choices=[
                            ("anthropic", "Anthropic (Claude)"),
                            ("zai", "Z.AI (GLM)"),
                            ("openrouter", "OpenRouter"),
                            ("ollama", "Ollama (local)"),
                            ("custom", "Custom endpoint"),
                        ],
                        help_text="Which backend this profile talks to",
                        max_length=20,
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Display name, e.g. 'Claude subscription' or 'My Z.AI plan'",
                        max_length=100,
                        unique=True,
                    ),
                ),
                (
                    "base_url",
                    models.CharField(
                        blank=True,
                        help_text="Anthropic-compatible endpoint URL. Blank for Anthropic itself.",
                        max_length=255,
                    ),
                ),
                (
                    "auth_method",
                    models.CharField(
                        choices=[
                            ("subscription", "Claude subscription (OAuth token)"),
                            ("api_key", "API key"),
                        ],
                        default="api_key",
                        help_text="Only meaningful for Anthropic (subscription OAuth vs API key); all other providers are token-only",
                        max_length=20,
                    ),
                ),
                (
                    "credential_encrypted",
                    models.TextField(
                        blank=True,
                        help_text="Token / API key (encrypted). Optional for Ollama.",
                    ),
                ),
                (
                    "default_model",
                    models.CharField(
                        blank=True,
                        help_text="Model id used when this provider is active (blank = account/endpoint default)",
                        max_length=100,
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
                "verbose_name": "AI provider",
                "verbose_name_plural": "AI providers",
                "db_table": "ai_providers",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="claudeusage",
            name="provider",
            field=models.CharField(blank=True, default="", max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="globalsettings",
            name="active_ai_provider",
            field=models.ForeignKey(
                blank=True,
                help_text="Provider profile used by scripts, Py AI, and connection tests",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="core.aiprovider",
            ),
        ),
        migrations.AlterField(
            model_name="globalsettings",
            name="claude_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Make AI available to scripts (via the pyrunner_ai helper)",
            ),
        ),
        migrations.AlterField(
            model_name="globalsettings",
            name="pyai_model",
            field=models.CharField(
                blank=True,
                help_text="Optional model id for Py AI (must match the active provider). Blank = the active provider's default model.",
                max_length=100,
            ),
        ),
        # Seed provider rows from the old fields BEFORE removing them.
        migrations.RunPython(seed_providers, unseed_providers),
        migrations.RemoveField(
            model_name="globalsettings",
            name="claude_api_key_encrypted",
        ),
        migrations.RemoveField(
            model_name="globalsettings",
            name="claude_auth_method",
        ),
        migrations.RemoveField(
            model_name="globalsettings",
            name="claude_default_model",
        ),
        migrations.RemoveField(
            model_name="globalsettings",
            name="claude_last_tested_at",
        ),
        migrations.RemoveField(
            model_name="globalsettings",
            name="claude_oauth_token_encrypted",
        ),
    ]
