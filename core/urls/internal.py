"""
Internal (loopback-only) URL patterns — Seam 1 datastore API.

Mounted at /internal/. These routes are NOT part of the public token-auth REST
API (/api/v1/); they are reached only by PyRunner's own worker over loopback,
authenticated by a signed per-run token. The /internal/ prefix is exempted from
the SSL redirect and the setup-wizard gate in settings (see SECURE_REDIRECT_EXEMPT
and SetupWizardMiddleware.STATIC_ALLOWED_PREFIXES) so a loopback call is never
301'd to https or 302'd to /setup/.
"""

from django.urls import path

from core.views.api.channels_internal import send as channels_send
from core.views.api.databases_internal import list_databases, resolve_dsn
from core.views.api.datastore_internal import (
    entries,
    entry,
    record_claude_usage,
    resolve_store,
)

app_name = "internal"

urlpatterns = [
    path("datastores/<str:name>", resolve_store, name="resolve_store"),
    path("datastores/<str:name>/entries", entries, name="entries"),
    path("datastores/<str:name>/entry", entry, name="entry"),
    path("claude-usage", record_claude_usage, name="claude_usage"),
    path("channels/send", channels_send, name="channels_send"),
    path("databases", list_databases, name="databases_list"),
    path("databases/<str:name>/dsn", resolve_dsn, name="databases_dsn"),
]
