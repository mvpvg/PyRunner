"""
URL configuration for pyrunner project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

import importlib
import logging

from django.conf import settings
from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

from core.views.webhooks import webhook_trigger_view

logger = logging.getLogger(__name__)


def get_admin_url_slug():
    """
    Get admin URL slug from settings.
    Called at startup - changes require app restart.
    """
    try:
        from core.models import GlobalSettings
        return GlobalSettings.get_settings().admin_url_slug or "django-admin"
    except Exception:
        return "django-admin"


urlpatterns = [
    path(f"{get_admin_url_slug()}/", admin.site.urls),
    path("setup/", include("core.urls.setup")),
    path("auth/", include("core.urls.auth")),
    path("cpanel/", include("core.urls.cpanel")),
    # REST API endpoints (token auth required)
    path("api/v1/", include("core.urls.api")),
    # Internal loopback-only datastore API (Seam 1). Signed per-run token auth;
    # exempt from SSL redirect + setup gate (see settings). Inert in Phase A —
    # the script helper still uses SQLite directly until the Stage 2 cutover.
    path("internal/", include("core.urls.internal")),
    # Public webhook endpoint (no auth required)
    path("webhook/<str:token>/", webhook_trigger_view, name="webhook_trigger"),
    path("", lambda request: redirect("auth:login")),
]


# Auto-mount each loaded plugin at /plugins/<slug>/. Each mount is guarded: a
# plugin whose urls.py (or a view it imports) is broken simply doesn't mount —
# core routes are unaffected. The set of plugins here is already constrained to
# those that passed the guarded loader in settings.py.
for _app in getattr(settings, "INSTALLED_PLUGINS", []):
    _slug = _app.rsplit(".", 1)[-1]
    try:
        importlib.import_module(f"{_app}.urls")
        urlpatterns.append(path(f"plugins/{_slug}/", include(f"{_app}.urls")))
    except ModuleNotFoundError as _exc:
        # No urls.py at all is fine (a plugin may have no routes); anything else
        # is a real failure to skip.
        if _exc.name not in (f"{_app}.urls",):
            logger.exception("Plugin %r URL mount failed; skipping", _slug)
    except Exception:
        logger.exception("Plugin %r URL mount failed; skipping", _slug)
