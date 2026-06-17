"""
Sales Dashboard plugin views.

Reads the orders saved by the "Sales Collector" script into the ``sales_data``
DataStore and renders a dashboard. Also triggers a fresh collector run. The
plugin reads the DataStore via core models (it runs in the web process); the
script writes it via ``pyrunner_datastore`` (it runs in an environment's venv).
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.models import DataStore, Run, Script

STORE_NAME = "sales_data"
SCRIPT_NAME = "Sales Collector"


def _load(key, default):
    store = DataStore.objects.filter(name=STORE_NAME).first()
    if store is None:
        return None, default
    entry = store.entries.filter(key=key).first()
    return store, (entry.get_value() if entry else default)


@login_required
def index(request):
    store, orders = _load("orders", [])
    _, last_run = _load("last_run", None)

    total_revenue = round(sum(o.get("revenue", 0) for o in orders), 2)
    order_count = len(orders)
    avg_order = round(total_revenue / order_count, 2) if order_count else 0
    units = sum(o.get("qty", 0) for o in orders)

    by_product = {}
    for o in orders:
        name = o.get("product", "—")
        row = by_product.setdefault(name, {"product": name, "qty": 0, "revenue": 0.0})
        row["qty"] += o.get("qty", 0)
        row["revenue"] = round(row["revenue"] + o.get("revenue", 0), 2)
    products = sorted(by_product.values(), key=lambda r: r["revenue"], reverse=True)

    recent = list(reversed(orders))[:15]

    return render(
        request,
        "sales_dashboard/index.html",
        {
            "store_exists": store is not None,
            "total_revenue": total_revenue,
            "order_count": order_count,
            "avg_order": avg_order,
            "units": units,
            "products": products,
            "recent": recent,
            "last_run": last_run,
            "script": Script.objects.filter(name=SCRIPT_NAME).first(),
        },
    )


@login_required
@require_POST
def run_collector(request):
    from core.tasks import queue_script_run

    script = Script.objects.filter(name=SCRIPT_NAME).first()
    if script is None:
        messages.error(request, f"Script '{SCRIPT_NAME}' not found — create it first.")
        return redirect("sales_dashboard:index")
    if not script.can_run:
        messages.error(request, f"Script '{SCRIPT_NAME}' is disabled or archived.")
        return redirect("sales_dashboard:index")

    run = Run.objects.create(
        script=script,
        status=Run.Status.PENDING,
        triggered_by=request.user,
        code_snapshot=script.code,
    )
    queue_script_run(run)
    messages.info(
        request, "Sales Collector queued — refresh in a few seconds to see new data."
    )
    return redirect("sales_dashboard:index")
