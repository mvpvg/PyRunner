"""
Qdrant Backup Monitor — plugin views.

Reads the backup run history written by the "Qdrant Backup" script into the
``qdrant_backups`` DataStore and renders a monitoring dashboard: health status,
KPIs, per-run history, and per-collection sizes. Also triggers a fresh backup.

The plugin READS the DataStore via core models (it runs in the web process); the
script WRITES it via ``pyrunner_datastore`` (it runs in an environment's venv).
See README.md for how the pieces connect.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.models import DataStore, Run, Script

STORE_NAME = "qdrant_backups"
SCRIPT_NAME = "Qdrant Backup"
HISTORY_LIMIT = 50

# status -> (label, pill classes, dot classes) for the run-history badges.
_BADGES = {
    "success": ("Success", "bg-ok/10 text-ok", "bg-ok"),
    "partial": ("Partial", "bg-warn/10 text-warn", "bg-warn"),
    "failed": ("Failed", "bg-fail/10 text-fail", "bg-fail"),
}


def _badge(status):
    label, cls, dot = _BADGES.get(status, ("Unknown", "bg-panel-hi text-muted", "bg-muted"))
    return {"label": label, "cls": cls, "dot": dot}


def _load_runs():
    """Return (store_or_None, runs_list). Defensive against missing/garbage data."""
    store = DataStore.objects.filter(name=STORE_NAME).first()
    if store is None:
        return None, []
    entry = store.entries.filter(key="runs").first()
    runs = entry.get_value() if entry else []
    if not isinstance(runs, list):
        runs = []
    return store, runs


def _record(raw):
    """Normalize one stored run dict into the shape the template expects."""
    status = raw.get("status", "unknown")
    return {
        "ts": raw.get("ts", "—"),
        "status": status,
        "badge": _badge(status),
        "collection_count": raw.get("collection_count", 0),
        "failed_count": raw.get("failed_count", 0),
        "total_size_mb": round(raw.get("total_size_mb", 0) or 0, 2),
        "duration_s": round(raw.get("duration_s", 0) or 0, 1),
        "deleted_old": raw.get("deleted_old", 0),
        "error": raw.get("error", ""),
        "collections": [c for c in raw.get("collections", []) if isinstance(c, dict)],
    }


@login_required
def index(request):
    store, runs = _load_runs()

    clean = [r for r in runs if isinstance(r, dict)]
    # newest first, capped for display
    history = [_record(r) for r in reversed(clean[-HISTORY_LIMIT:])]
    latest = history[0] if history else None

    # success rate across all stored runs
    total_runs = len(clean)
    success_runs = sum(1 for r in clean if r.get("status") == "success")
    success_rate = round(100 * success_runs / total_runs) if total_runs else 0

    # per-collection sizes from the latest run, with a delta vs the previous run
    collections = []
    if latest:
        prev = history[1] if len(history) > 1 else None
        prev_sizes = {}
        if prev:
            prev_sizes = {c.get("collection"): c.get("size_mb", 0) for c in prev["collections"]}
        for c in latest["collections"]:
            name = c.get("collection", "—")
            size = round(c.get("size_mb", 0) or 0, 2)
            prev_size = prev_sizes.get(name)
            collections.append({
                "collection": name,
                "size_mb": size,
                "status": c.get("status", "ok"),
                "error": c.get("error", ""),
                "delta": None if prev_size is None else round(size - prev_size, 2),
            })

    return render(request, "qdrant_backup_monitor/index.html", {
        "store_exists": store is not None,
        "has_data": bool(history),
        "latest": latest,
        "history": history,
        "collections": collections,
        "success_rate": success_rate,
        "success_runs": success_runs,
        "total_runs": total_runs,
        "script": Script.objects.filter(name=SCRIPT_NAME).first(),
        "store_name": STORE_NAME,
        "script_name": SCRIPT_NAME,
    })


@login_required
@require_POST
def run_backup(request):
    from core.tasks import queue_script_run

    script = Script.objects.filter(name=SCRIPT_NAME).first()
    if script is None:
        messages.error(request, f"Script '{SCRIPT_NAME}' not found — create it first.")
        return redirect("qdrant_backup_monitor:index")
    if not script.can_run:
        messages.error(request, f"Script '{SCRIPT_NAME}' is disabled or archived.")
        return redirect("qdrant_backup_monitor:index")

    run = Run.objects.create(
        script=script,
        status=Run.Status.PENDING,
        triggered_by=request.user,
        code_snapshot=script.code,
    )
    queue_script_run(run)
    messages.info(
        request,
        "Qdrant Backup queued — it can take a while for large collections. "
        "Refresh once it finishes to see the new run.",
    )
    return redirect("qdrant_backup_monitor:index")
