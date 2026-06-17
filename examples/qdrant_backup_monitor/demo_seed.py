"""
Demo seed for the Qdrant Backup Monitor — populate the `qdrant_backups`
DataStore with realistic, fake backup history so you can test the dashboard UI
WITHOUT configuring real Qdrant / R2 / Resend secrets.

Setup in PyRunner:
  1. Data Stores -> create one named exactly  qdrant_backups
  2. Scripts -> create a script (any name), paste this, pick any environment, Run
  3. Open "Qdrant Backups" in the sidebar — the dashboard is now populated

Run it again to append another fresh run. Delete this script when you're done;
it only writes to the DataStore (no real backups are performed).

`pyrunner_datastore` is provided by PyRunner automatically — no install needed.
"""

import datetime
import random

from pyrunner_datastore import DataStore

STORE = "qdrant_backups"
COLLECTIONS = ["documents", "embeddings", "images", "products"]
HISTORY_LIMIT = 50

store = DataStore(STORE)

runs = store.get("runs", [])
if not isinstance(runs, list):
    runs = []


def make_run(when, *, fail=None):
    """Build one run record. `fail` is a collection name to mark failed, or None."""
    cols = []
    failed = 0
    total = 0.0
    for name in COLLECTIONS:
        if name == fail:
            cols.append({
                "collection": name,
                "size_mb": 0.0,
                "status": "failed",
                "error": "ConnectionError: snapshot download timed out after 300s",
            })
            failed += 1
        else:
            size = round(random.uniform(8, 95), 2)
            total += size
            cols.append({"collection": name, "size_mb": size, "status": "ok", "error": ""})
    if failed == len(COLLECTIONS):
        status = "failed"
    elif failed:
        status = "partial"
    else:
        status = "success"
    return {
        "ts": when.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "duration_s": round(random.uniform(8, 42), 1),
        "collection_count": len(COLLECTIONS) - failed,
        "failed_count": failed,
        "total_size_mb": round(total, 2),
        "deleted_old": random.choice([0, 0, 1, 2]),
        "error": "",
        "collections": cols,
    }


# Backfill ~12 daily runs the first time, so the dashboard looks alive.
if not runs:
    base = datetime.datetime.now() - datetime.timedelta(days=12)
    for d in range(12):
        when = base + datetime.timedelta(days=d, minutes=random.randint(0, 45))
        # exercise the "partial" UI state on one historical day
        runs.append(make_run(when, fail="images" if d == 4 else None))

# Always append one fresh, healthy run so "last run" is current.
runs.append(make_run(datetime.datetime.now()))

store["runs"] = runs[-HISTORY_LIMIT:]
print(f"Seeded {len(runs)} run(s) into DataStore '{STORE}'. Open 'Qdrant Backups' in the sidebar.")
