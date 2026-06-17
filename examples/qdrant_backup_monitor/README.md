# Qdrant Backup Monitor (plugin + script)

A real-world PyRunner plugin: a **monitoring dashboard** for the Qdrant → Cloudflare
R2 backup script. It follows the canonical PyRunner pattern — **a script produces
data, a plugin visualizes it** — so the dashboard never imports `boto3`/`requests`
into the web process. The script (running in an environment's venv) records each
run into a DataStore; the plugin (running in the web process) reads it.

## What the dashboard shows

- **Health banner** — green / amber / red for the last run (healthy · partial · failed), with the failure error inline.
- **KPIs** — collections backed up, total size, duration, and success rate across all recorded runs.
- **Backup history** — a per-run table (when · status · collections · failures · size · duration).
- **Latest collections** — per-collection size, status, and the size delta vs. the previous run.
- **Run backup now** — queues the `Qdrant Backup` script straight from the dashboard.

## How the pieces connect

```
Qdrant Backup (backup_script.py — runs in an env venv with requests/boto3/resend)
   └─ from pyrunner_datastore import DataStore        # PyRunner-provided
       └─ record_run(...) appends each run ──►  DataStore "qdrant_backups"
                                                       ▲
Qdrant Backup Monitor (this plugin — runs in the web process)
   └─ from core.models import DataStore               # reads the same store
```

`backup_script.py` is your original Qdrant→R2 script; the only addition is
`record_run(...)`, which writes a compact record on **every** run — success,
partial, or failure — so all three dashboard states are real.

## Files

| File | Where it runs | Purpose |
|---|---|---|
| `apps.py`, `urls.py`, `views.py`, `templates/` | web process | the plugin (the dashboard) |
| `backup_script.py` | environment venv | the real backup, now reporting to the dashboard |
| `demo_seed.py` | environment venv | optional — fake history to test the UI with no secrets |

## Setup

1. **Data Stores** → create one named exactly `qdrant_backups`.
2. **Plugins** → upload `qdrant_backup_monitor.zip` (a single top-level
   `qdrant_backup_monitor/` folder), **Activate**, then **Restart**.
3. Choose one of:
   - **Test the UI now (no secrets):** Scripts → new script, paste
     [`demo_seed.py`](demo_seed.py), pick any environment, **Run**. Open
     **Qdrant Backups** in the sidebar — it's populated with sample history.
   - **Wire up the real backup:** Scripts → new script named exactly
     `Qdrant Backup`, paste [`backup_script.py`](backup_script.py); add the 7
     secrets (`QDRANT_URL`, `QDRANT_API_KEY`, `R2_ENDPOINT_URL`,
     `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`,
     `RESEND_API_KEY`); pick an environment with `requests boto3 resend`; **Run**
     (or use the dashboard's "Run backup now").

> The plugin looks up the script by the name `Qdrant Backup` and the store by
> `qdrant_backups`. Keep those names, or edit `STORE_NAME` / `SCRIPT_NAME` in
> `views.py` **and** `backup_script.py`.

## Packaging the zip

From the repo root:

```bash
cd examples
zip -r qdrant_backup_monitor.zip qdrant_backup_monitor -x '*/__pycache__/*'
```

The archive must contain a single top-level folder (`qdrant_backup_monitor/`) —
that's the slug, and it must match `plugin.json`'s `slug` and the `apps.py`
descriptor.

See `docs/plugins.md` for the full author guide.
