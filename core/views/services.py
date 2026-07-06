"""
Services management views for the control panel.
"""

import json
import logging
from datetime import timedelta

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.http import HttpRequest, HttpResponse, JsonResponse

from core.models import AIProvider, GlobalSettings, ClaudeUsage, PROVIDER_PRESETS
from core.forms import S3SettingsForm, AISettingsForm, AIProviderForm
from core.services.s3_service import S3Service
from core.services.claude_service import ClaudeService
from core.services.encryption_service import EncryptionService

logger = logging.getLogger(__name__)


def superuser_required(view_func):
    """Decorator to require superuser status for S3 configuration."""
    return user_passes_test(lambda u: u.is_superuser, login_url="auth:login")(view_func)


@login_required
@superuser_required
def services_view(request: HttpRequest) -> HttpResponse:
    """Display services configuration page."""
    settings = GlobalSettings.get_settings()
    s3_form = S3SettingsForm(instance=settings)
    s3_status = S3Service.get_status()
    claude_form = AISettingsForm(instance=settings)
    claude_status = ClaudeService.get_status()

    providers = list(AIProvider.objects.all())
    # Plain-dict mirror for the template JS (edit prefill, per-type hints).
    providers_data = [
        {
            "id": str(p.id),
            "name": p.name,
            "provider_type": p.provider_type,
            "base_url": p.base_url,
            "auth_method": p.auth_method,
            "default_model": p.default_model,
            "has_credential": bool(p.credential_encrypted),
        }
        for p in providers
    ]
    presets_data = {str(key): value for key, value in PROVIDER_PRESETS.items()}

    return render(
        request,
        "cpanel/services/list.html",
        {
            "settings": settings,
            "s3_form": s3_form,
            "s3_status": s3_status,
            "claude_form": claude_form,
            "claude_status": claude_status,
            "ai_providers": providers,
            "ai_provider_form": AIProviderForm(),
            "ai_providers_json": providers_data,
            "ai_presets_json": presets_data,
        },
    )


@login_required
@superuser_required
@require_POST
def s3_settings_view(request: HttpRequest) -> HttpResponse:
    """Update S3 storage settings."""
    settings = GlobalSettings.get_settings()
    form = S3SettingsForm(request.POST, instance=settings)

    if form.is_valid():
        form.save(settings)
        messages.success(request, "S3 storage settings saved successfully.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")

    return redirect("cpanel:services")


@login_required
@superuser_required
@require_POST
def s3_test_connection_view(request: HttpRequest) -> JsonResponse:
    """Test S3 connection and return result.

    Accepts form data in POST body to test credentials before saving.
    Falls back to saved settings if no form data provided.
    """
    try:
        # Try to parse form data from request body
        data = {}
        if request.body:
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse(
                    {"success": False, "error": "Invalid JSON in request body"},
                    status=400,
                )

        if data:
            # Test with provided form data
            settings = GlobalSettings.get_settings()

            # Get credentials from form or fall back to saved encrypted values
            access_key = data.get("s3_access_key", "")
            if not access_key and settings.s3_access_key_encrypted:
                access_key = EncryptionService.decrypt(settings.s3_access_key_encrypted)

            secret_key = data.get("s3_secret_key", "")
            if not secret_key and settings.s3_secret_key_encrypted:
                secret_key = EncryptionService.decrypt(settings.s3_secret_key_encrypted)

            success, message = S3Service.test_connection_with_credentials(
                bucket_name=data.get("s3_bucket_name", ""),
                access_key=access_key,
                secret_key=secret_key,
                endpoint_url=data.get("s3_endpoint_url", ""),
                region=data.get("s3_region", "us-east-1"),
                use_ssl=data.get("s3_use_ssl", True),
                path_style=data.get("s3_path_style", False),
            )
        else:
            # Fall back to testing saved settings
            success, message = S3Service.test_connection()

        return JsonResponse(
            {
                "success": success,
                "message": message if success else None,
                "error": message if not success else None,
            }
        )
    except Exception as e:
        logger.exception("S3 connection test failed")
        return JsonResponse(
            {
                "success": False,
                "error": str(e),
            }
        )


@login_required
@superuser_required
@require_POST
def claude_settings_view(request: HttpRequest) -> HttpResponse:
    """Update AI integration settings (master toggle + active provider)."""
    settings = GlobalSettings.get_settings()
    form = AISettingsForm(request.POST, instance=settings)

    if form.is_valid():
        form.save(settings)
        messages.success(request, "AI settings saved successfully.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")

    return redirect("cpanel:services")


@login_required
@superuser_required
@require_POST
def ai_provider_save_view(request: HttpRequest) -> HttpResponse:
    """Create or update an AIProvider profile (hidden provider_id = edit)."""
    instance = None
    provider_id = request.POST.get("provider_id")
    if provider_id:
        instance = AIProvider.objects.filter(pk=provider_id).first()
        if instance is None:
            messages.error(request, "Provider not found.")
            return redirect("cpanel:services")

    form = AIProviderForm(request.POST, instance=instance)
    if form.is_valid():
        provider = form.save()
        # Convenience: the first provider ever saved becomes active.
        settings = GlobalSettings.get_settings()
        if settings.active_ai_provider_id is None and AIProvider.objects.count() == 1:
            settings.active_ai_provider = provider
            settings.save(update_fields=["active_ai_provider"])
        messages.success(request, f"Provider '{provider.name}' saved.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")

    return redirect("cpanel:services")


@login_required
@superuser_required
@require_POST
def ai_provider_delete_view(request: HttpRequest, provider_id) -> HttpResponse:
    """Delete a provider profile (active FK falls back to none via SET_NULL)."""
    provider = AIProvider.objects.filter(pk=provider_id).first()
    if provider is None:
        messages.error(request, "Provider not found.")
        return redirect("cpanel:services")

    was_active = (
        GlobalSettings.get_settings().active_ai_provider_id == provider.id
    )
    name = provider.name
    provider.delete()
    if was_active:
        messages.warning(
            request,
            f"Provider '{name}' deleted. No provider is active now — AI is "
            "effectively off until you activate another one.",
        )
    else:
        messages.success(request, f"Provider '{name}' deleted.")
    return redirect("cpanel:services")


@login_required
@superuser_required
@require_POST
def ai_provider_activate_view(request: HttpRequest, provider_id) -> HttpResponse:
    """Make one saved provider the active one (one-click switch)."""
    provider = AIProvider.objects.filter(pk=provider_id).first()
    if provider is None:
        messages.error(request, "Provider not found.")
        return redirect("cpanel:services")

    settings = GlobalSettings.get_settings()
    settings.active_ai_provider = provider
    settings.save(update_fields=["active_ai_provider"])
    messages.success(request, f"'{provider.name}' is now the active AI provider.")
    return redirect("cpanel:services")


@login_required
@superuser_required
@require_POST
def claude_test_connection_view(request: HttpRequest) -> JsonResponse:
    """Test an AI provider connection with a real SDK round-trip.

    Three request shapes:
    - {"provider_id": ...} — test a saved provider row (optional "credential"
      override for edit-before-save);
    - {"provider_type": ..., "credential": ..., ...} — test unsaved form values;
    - {} — test the currently-active provider.
    """
    try:
        data = {}
        if request.body:
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse(
                    {"success": False, "error": "Invalid JSON in request body"},
                    status=400,
                )

        provider_id = data.get("provider_id")
        if provider_id:
            provider = AIProvider.objects.filter(pk=provider_id).first()
            if provider is None:
                return JsonResponse({"success": False, "error": "Provider not found."})
            overrides = {"provider_type", "base_url", "default_model", "auth_method"}
            if overrides & set(data):
                # Edit-form test: unsaved field values, saved credential fallback.
                credential = data.get("credential", "")
                if not credential and provider.credential_encrypted:
                    credential = EncryptionService.decrypt(provider.credential_encrypted)
                ptype = data.get("provider_type") or provider.provider_type
                preset = PROVIDER_PRESETS.get(ptype, {})
                success, message = ClaudeService.test_connection_with_credentials(
                    ptype,
                    credential,
                    auth_method=data.get("auth_method") or provider.auth_method,
                    base_url=data.get("base_url")
                    or provider.base_url
                    or preset.get("base_url", ""),
                    model=data.get("default_model", provider.default_model),
                    extra_env=preset.get("extra_env"),
                )
            else:
                success, message = ClaudeService.test_provider(
                    provider, credential_override=data.get("credential", "")
                )
        elif data.get("provider_type"):
            ptype = data["provider_type"]
            preset = PROVIDER_PRESETS.get(ptype, {})
            success, message = ClaudeService.test_connection_with_credentials(
                ptype,
                data.get("credential", ""),
                auth_method=data.get("auth_method") or AIProvider.AuthMethod.API_KEY,
                base_url=data.get("base_url") or preset.get("base_url", ""),
                model=data.get("default_model", ""),
                extra_env=preset.get("extra_env"),
            )
        else:
            success, message = ClaudeService.test_saved_connection()

        return JsonResponse(
            {
                "success": success,
                "message": message if success else None,
                "error": message if not success else None,
            }
        )
    except Exception as e:
        logger.exception("AI provider connection test failed")
        return JsonResponse({"success": False, "error": str(e)})


@login_required
@superuser_required
def claude_usage_view(request: HttpRequest) -> HttpResponse:
    """Claude usage analytics: token totals, daily chart, and per-call rows."""
    period = request.GET.get("period", "30")
    day_map = {"7": 7, "30": 30, "90": 90}

    base = ClaudeUsage.objects.all()
    if period in day_map:
        since = timezone.now() - timedelta(days=day_map[period])
        base = base.filter(created_at__gte=since)
        period_label = f"Last {day_map[period]} days"
    else:
        period = "all"
        period_label = "All time"

    # Summary totals
    agg = base.aggregate(
        requests=Count("id"),
        input=Sum("input_tokens"),
        output=Sum("output_tokens"),
        cache_creation=Sum("cache_creation_tokens"),
        cache_read=Sum("cache_read_tokens"),
    )
    inp = agg["input"] or 0
    out = agg["output"] or 0
    cache_write = agg["cache_creation"] or 0
    cache_read = agg["cache_read"] or 0
    cache = cache_write + cache_read
    summary = {
        "requests": agg["requests"] or 0,
        "input": inp,
        "output": out,
        "cache": cache,
        "cache_write": cache_write,
        "cache_read": cache_read,
        "total": inp + out + cache,
    }

    # Daily series for the chart
    daily = list(
        base.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            input=Sum("input_tokens"),
            output=Sum("output_tokens"),
            requests=Count("id"),
        )
        .order_by("day")
    )
    chart = {
        "labels": [d["day"].strftime("%b %d") if d["day"] else "" for d in daily],
        "input": [d["input"] or 0 for d in daily],
        "output": [d["output"] or 0 for d in daily],
        "requests": [d["requests"] or 0 for d in daily],
    }

    # Per-model breakdown (split by serving provider)
    by_model = []
    for row in (
        base.values("provider", "model")
        .annotate(requests=Count("id"), input=Sum("input_tokens"), output=Sum("output_tokens"))
        .order_by("-input")
    ):
        by_model.append(
            {
                "provider": row["provider"] or "",
                "model": row["model"] or "(unknown)",
                "requests": row["requests"],
                "input": row["input"] or 0,
                "output": row["output"] or 0,
                "total": (row["input"] or 0) + (row["output"] or 0),
            }
        )

    # Top scripts by tokens
    by_script = []
    for row in (
        base.filter(script_id__isnull=False)
        .values("script_id", "script_name")
        .annotate(requests=Count("id"), input=Sum("input_tokens"), output=Sum("output_tokens"))
        .order_by("-input")[:10]
    ):
        by_script.append(
            {
                "script_id": row["script_id"],
                "script_name": row["script_name"] or "(unnamed)",
                "requests": row["requests"],
                "total": (row["input"] or 0) + (row["output"] or 0),
            }
        )

    # Recent rows (paginated)
    paginator = Paginator(base.order_by("-created_at"), 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "cpanel/services/usage.html",
        {
            "period": period,
            "period_label": period_label,
            "period_options": [("7", "7d"), ("30", "30d"), ("90", "90d"), ("all", "All")],
            "summary": summary,
            "chart_json": json.dumps(chart),
            "has_data": summary["requests"] > 0,
            "by_model": by_model,
            "by_script": by_script,
            "page_obj": page_obj,
            "claude_status": ClaudeService.get_status(),
        },
    )
