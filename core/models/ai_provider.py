"""
AI provider profiles.

One row per configured provider credential (Anthropic subscription, Anthropic
API key, Z.AI, OpenRouter, Ollama, or any custom Anthropic-compatible
endpoint). All rows persist their keys (encrypted) so switching the active
provider is one click — the active one is pointed to by
``GlobalSettings.active_ai_provider``.

Routing works because the Claude Code CLI (spawned by claude-agent-sdk)
selects its backend purely from env vars: ``ANTHROPIC_BASE_URL`` +
``ANTHROPIC_AUTH_TOKEN`` for third-party Anthropic-compatible endpoints, or
the native credential vars for Anthropic itself. Env building lives in
``core.services.claude_service.ClaudeService``.
"""

import uuid

from django.db import models


class AIProvider(models.Model):
    class ProviderType(models.TextChoices):
        ANTHROPIC = "anthropic", "Anthropic (Claude)"
        ZAI = "zai", "Z.AI (GLM)"
        OPENROUTER = "openrouter", "OpenRouter"
        OLLAMA = "ollama", "Ollama (local)"
        CUSTOM = "custom", "Custom endpoint"

    class AuthMethod(models.TextChoices):
        SUBSCRIPTION = "subscription", "Claude subscription (OAuth token)"
        API_KEY = "api_key", "API key"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider_type = models.CharField(
        max_length=20,
        choices=ProviderType.choices,
        help_text="Which backend this profile talks to",
    )
    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Display name, e.g. 'Claude subscription' or 'My Z.AI plan'",
    )
    base_url = models.CharField(
        max_length=255,
        blank=True,
        help_text="Anthropic-compatible endpoint URL. Blank for Anthropic itself.",
    )
    auth_method = models.CharField(
        max_length=20,
        choices=AuthMethod.choices,
        default=AuthMethod.API_KEY,
        help_text="Only meaningful for Anthropic (subscription OAuth vs API key); "
        "all other providers are token-only",
    )
    credential_encrypted = models.TextField(
        blank=True,
        help_text="Token / API key (encrypted). Optional for Ollama.",
    )
    default_model = models.CharField(
        max_length=100,
        blank=True,
        help_text="Model id used when this provider is active (blank = account/endpoint default)",
    )
    last_tested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this provider's connection last tested successfully",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_providers"
        ordering = ["name"]
        verbose_name = "AI provider"
        verbose_name_plural = "AI providers"

    def __str__(self):
        return f"{self.name} ({self.get_provider_type_display()})"

    @property
    def preset(self) -> dict:
        return PROVIDER_PRESETS[self.provider_type]

    @property
    def is_anthropic(self) -> bool:
        return self.provider_type == self.ProviderType.ANTHROPIC

    @property
    def credential_required(self) -> bool:
        # Ollama's endpoint only needs a non-empty bearer token; we default one.
        return self.provider_type != self.ProviderType.OLLAMA


# Static per-type defaults and UI hints. Code-level by design — the DB stores
# only what the user entered; changing a preset here updates all instances.
PROVIDER_PRESETS = {
    AIProvider.ProviderType.ANTHROPIC: {
        "label": "Anthropic (Claude)",
        "base_url": "",
        "base_url_editable": False,
        "extra_env": {},
        "default_credential": "",
        "model_placeholder": "claude-sonnet-4-6 (optional)",
        "credential_label": "Credential",
        "credential_help": "Subscription token from `claude setup-token`, or an API key from console.anthropic.com.",
        "docs_url": "https://docs.claude.com/en/docs/claude-code/setup",
        "notes": "",
    },
    AIProvider.ProviderType.ZAI: {
        "label": "Z.AI (GLM)",
        "base_url": "https://api.z.ai/api/anthropic",
        "base_url_editable": True,
        # Officially recommended by Z.AI: GLM can be slow to first token.
        "extra_env": {"API_TIMEOUT_MS": "3000000"},
        "default_credential": "",
        "model_placeholder": "glm-5.2",
        "credential_label": "Z.AI API key",
        "credential_help": "From z.ai — a GLM Coding Plan key or a standard API key.",
        "docs_url": "https://docs.z.ai/devpack/tool/claude",
        "notes": "Coding Plan quota is contractually scoped to coding tools; standard API keys have no such scoping.",
    },
    AIProvider.ProviderType.OPENROUTER: {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api",
        "base_url_editable": True,
        "extra_env": {},
        "default_credential": "",
        "model_placeholder": "anthropic/claude-sonnet-4-6",
        "credential_label": "OpenRouter API key",
        "credential_help": "From openrouter.ai/keys. Model ids are namespaced, e.g. deepseek/deepseek-chat.",
        "docs_url": "https://openrouter.ai/docs/guides/community/anthropic-agent-sdk",
        "notes": "Tool-calling reliability varies by model; run Test after picking one.",
    },
    AIProvider.ProviderType.OLLAMA: {
        "label": "Ollama (local)",
        "base_url": "http://localhost:11434",
        "base_url_editable": True,
        "extra_env": {},
        # The endpoint just needs a non-empty bearer token.
        "default_credential": "ollama",
        "model_placeholder": "qwen3-coder",
        "credential_label": "Token (optional)",
        "credential_help": "Usually not needed — any non-empty value works. Requires Ollama ≥ 0.14.",
        "docs_url": "https://docs.ollama.com/api/anthropic-compatibility",
        "notes": "From Docker, use http://host.docker.internal:11434 to reach Ollama on the host.",
    },
    AIProvider.ProviderType.CUSTOM: {
        "label": "Custom endpoint",
        "base_url": "",
        "base_url_editable": True,
        "extra_env": {},
        "default_credential": "",
        "model_placeholder": "",
        "credential_label": "API key / token",
        "credential_help": "Any Anthropic-compatible endpoint (LiteLLM proxy, claude-code-router, llama.cpp server, …).",
        "docs_url": "",
        "notes": "",
    },
}
