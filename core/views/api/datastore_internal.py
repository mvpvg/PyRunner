"""
Internal DataStore API (Seam 1 — DB engine-agnostic foundation).

This is the *seam*, not the cutover. It exposes the full key-value surface the
script-side ``pyrunner_datastore`` helper needs, but entirely through the Django
ORM (``DataStore`` / ``DataStoreEntry``), so it is engine-agnostic for free —
identical on SQLite and, later, Postgres. In Phase A nothing calls it on the hot
path: the helper still reads SQLite directly (byte-for-byte with today). Stage 2
points the helper here once the endpoint is hardened end-to-end.

Contract notes (so the Stage-2 client can reproduce today's exceptions exactly):
- store missing            -> 404 STORE_NOT_FOUND  (client: ValueError)
- entry/key missing        -> 404 KEY_NOT_FOUND    (client: KeyError)
- values round-trip through ``DataStoreEntry.get_value()/set_value()`` — the same
  model API used everywhere else, so it survives the value_json -> JSONField swap.

Auth + isolation are provided by ``internal_datastore_token_required`` (loopback
only, stateless signed per-run token, NOT rate-limited).
"""

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.models import ClaudeUsage, DataStore, DataStoreEntry
from core.views.api.decorators import internal_datastore_token_required

logger = logging.getLogger(__name__)


def _store_not_found(name: str) -> JsonResponse:
    return JsonResponse(
        {"error": {"code": "STORE_NOT_FOUND", "message": f"Data store '{name}' does not exist"}},
        status=404,
    )


def _key_not_found(key: str) -> JsonResponse:
    return JsonResponse(
        {"error": {"code": "KEY_NOT_FOUND", "message": f"Key '{key}' not found"}},
        status=404,
    )


def _get_store(name: str, workspace_id):
    """Resolve a store by name within the run's workspace. Returns it or None.

    Names are unique per workspace (tenancy Decision 2B); ``workspace_id`` is
    derived server-side from the signed token's run, so a run in one workspace
    cannot reach another workspace's store by name.
    """
    try:
        return DataStore.resolve_for_workspace(name, workspace_id)
    except DataStore.DoesNotExist:
        return None


@csrf_exempt
@require_http_methods(["GET"])
@internal_datastore_token_required
def resolve_store(request: HttpRequest, name: str) -> JsonResponse:
    """GET /internal/datastores/<name> — confirm a store exists (mirrors the
    helper's constructor check that raises ValueError on a missing store)."""
    store = _get_store(name, request.datastore_workspace)
    if store is None:
        return _store_not_found(name)
    return JsonResponse({"name": store.name, "entry_count": store.entry_count})


@csrf_exempt
@require_http_methods(["GET", "DELETE"])
@internal_datastore_token_required
def entries(request: HttpRequest, name: str) -> JsonResponse:
    """Collection ops on a store's entries.

    GET    -> all entries [{key, value}] ordered by key (covers keys/values/items/len).
    DELETE -> clear all entries, returns {deleted: n}.
    """
    store = _get_store(name, request.datastore_workspace)
    if store is None:
        return _store_not_found(name)

    if request.method == "DELETE":
        deleted, _ = store.entries.all().delete()
        return JsonResponse({"deleted": deleted})

    rows = store.entries.order_by("key")
    return JsonResponse(
        {
            "entries": [{"key": e.key, "value": e.get_value()} for e in rows],
            "count": rows.count(),
        }
    )


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
@internal_datastore_token_required
def entry(request: HttpRequest, name: str) -> JsonResponse:
    """Single-entry ops on a store.

    GET    ?key=K           -> {key, value} or 404 KEY_NOT_FOUND.
    PUT    body {key,value}  -> upsert, returns {key, value}.
    DELETE ?key=K           -> {deleted: true} or 404 KEY_NOT_FOUND.
    """
    store = _get_store(name, request.datastore_workspace)
    if store is None:
        return _store_not_found(name)

    if request.method == "PUT":
        try:
            data = json.loads(request.body or b"{}")
        except (ValueError, TypeError):
            return JsonResponse(
                {"error": {"code": "BAD_REQUEST", "message": "Body must be valid JSON"}},
                status=400,
            )
        if not isinstance(data, dict) or "key" not in data or "value" not in data:
            return JsonResponse(
                {"error": {"code": "BAD_REQUEST", "message": "Body requires 'key' and 'value'"}},
                status=400,
            )
        key = data["key"]
        try:
            obj = DataStoreEntry.objects.get(datastore=store, key=key)
        except DataStoreEntry.DoesNotExist:
            obj = DataStoreEntry(datastore=store, key=key)
        obj.set_value(data["value"])
        obj.save()
        return JsonResponse({"key": obj.key, "value": obj.get_value()})

    # GET / DELETE both address a single key via query param so arbitrary key
    # characters never have to be path-encoded.
    key = request.GET.get("key")
    if key is None:
        return JsonResponse(
            {"error": {"code": "BAD_REQUEST", "message": "Missing 'key' query parameter"}},
            status=400,
        )

    try:
        obj = DataStoreEntry.objects.get(datastore=store, key=key)
    except DataStoreEntry.DoesNotExist:
        return _key_not_found(key)

    if request.method == "DELETE":
        obj.delete()
        return JsonResponse({"deleted": True})

    return JsonResponse({"key": obj.key, "value": obj.get_value()})


@csrf_exempt
@require_http_methods(["POST"])
@internal_datastore_token_required
def record_claude_usage(request: HttpRequest) -> JsonResponse:
    """POST /internal/claude-usage — record one Claude usage row.

    The ORM equivalent of pyrunner_ai's raw-sqlite usage write, used when there
    is no local DB file (Postgres). Best-effort telemetry: a bad row should not
    fail the caller's run, so a malformed body is a 400 the helper ignores.
    """
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse(
            {"error": {"code": "BAD_REQUEST", "message": "Body must be valid JSON"}},
            status=400,
        )

    ClaudeUsage.objects.create(
        script_id=data.get("script_id") or None,
        run_id=data.get("run_id") or None,
        script_name=data.get("script_name", "") or "",
        source=data.get("source") or ClaudeUsage.Source.SCRIPT,
        provider=data.get("provider", "") or "",
        model=data.get("model", "") or "",
        input_tokens=int(data.get("input_tokens") or 0),
        output_tokens=int(data.get("output_tokens") or 0),
        cache_creation_tokens=int(data.get("cache_creation_tokens") or 0),
        cache_read_tokens=int(data.get("cache_read_tokens") or 0),
        num_turns=int(data.get("num_turns") or 0),
        duration_ms=int(data.get("duration_ms") or 0),
        cost_usd=data.get("cost_usd"),
    )
    return JsonResponse({"ok": True})
