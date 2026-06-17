"""
=============================================================================
 QDRANT BACKUP TO CLOUDFLARE R2  —  PyRunner edition (with dashboard reporting)
=============================================================================

 This is the original Qdrant → R2 backup script, adapted for PyRunner so the
 "Qdrant Backup Monitor" plugin can visualize it. The only functional addition
 is `record_run(...)`: at the end of every run (success, partial, or failure) it
 appends a compact run record to the `qdrant_backups` DataStore. The plugin
 reads that DataStore and renders the monitoring dashboard.

 SETUP IN PYRUNNER
   1. Data Stores -> create one named exactly  qdrant_backups
   2. Scripts -> create a script named exactly  Qdrant Backup  and paste this
   3. Secrets -> add the 7 secrets listed below
   4. Environment -> one that has:  requests  boto3  resend
   5. Run it (or use the dashboard's "Run backup now" button)

 REQUIRED SECRETS (PyRunner Secrets Manager):
   QDRANT_URL, QDRANT_API_KEY, R2_ENDPOINT_URL, R2_ACCESS_KEY_ID,
   R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, RESEND_API_KEY

 REQUIRED PACKAGES (PyRunner environment): requests, boto3, resend
=============================================================================
"""

import os
import sys
import time
import requests
import boto3
import resend
from datetime import datetime, timedelta, timezone


# =============================================================================
# CONFIGURATION
# =============================================================================

QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ["QDRANT_API_KEY"]

R2_ENDPOINT_URL = os.environ["R2_ENDPOINT_URL"]
R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET_NAME = os.environ["R2_BUCKET_NAME"]

R2_BACKUP_PREFIX = "qdrant-backups"
RETENTION_DAYS = 30

RESEND_API_KEY = os.environ["RESEND_API_KEY"]
EMAIL_FROM = "pyrun@learnwithhasan.com"
EMAIL_TO = "hasan@learnwithhasan.com"

REQUEST_TIMEOUT = 300

# Dashboard reporting — the DataStore the "Qdrant Backup Monitor" plugin reads.
# Create it in the PyRunner UI (Data Stores) before running.
STORE_NAME = "qdrant_backups"
HISTORY_LIMIT = 50  # keep only the most recent N runs in the dashboard


# =============================================================================
# HELPER: Logging with timestamps
# =============================================================================

def log(message):
    """Print a timestamped log message to stdout."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


# =============================================================================
# DASHBOARD: record this run into the qdrant_backups DataStore (best effort)
# =============================================================================

def record_run(status, results, total_time, deleted_old=0, error=None):
    """Append a compact record of this run to the `qdrant_backups` DataStore.

    Best-effort: a missing/unavailable DataStore never breaks the backup itself —
    it only means the dashboard won't update for this run. `results` items use the
    same shape main() builds: {"collection", "size_mb", "s3_key", "error"?}.
    """
    try:
        from pyrunner_datastore import DataStore
    except Exception:
        # Not running under PyRunner (e.g. local testing) — skip silently.
        return

    try:
        store = DataStore(STORE_NAME)
    except Exception as exc:
        log(f"⚠️  Dashboard: DataStore '{STORE_NAME}' unavailable ({exc}). "
            "Create it in the PyRunner UI to enable monitoring.")
        return

    collections = []
    ok_count = 0
    failed_count = 0
    total_size = 0.0
    for r in results:
        is_ok = r.get("s3_key") != "FAILED" and not r.get("error")
        if is_ok:
            ok_count += 1
            total_size += r.get("size_mb", 0) or 0
        else:
            failed_count += 1
        collections.append({
            "collection": r.get("collection", "—"),
            "size_mb": round(r.get("size_mb", 0) or 0, 2),
            "status": "ok" if is_ok else "failed",
            "error": r.get("error", ""),
        })

    record = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,  # "success" | "partial" | "failed"
        "duration_s": round(total_time, 1),
        "collection_count": ok_count,
        "failed_count": failed_count,
        "total_size_mb": round(total_size, 2),
        "deleted_old": deleted_old,
        "error": str(error) if error else "",
        "collections": collections,
    }

    try:
        runs = store.get("runs", [])
        if not isinstance(runs, list):
            runs = []
        runs.append(record)
        store["runs"] = runs[-HISTORY_LIMIT:]
        log(f"📊 Dashboard: recorded run to '{STORE_NAME}' (status={status})")
    except Exception as exc:
        log(f"⚠️  Dashboard: failed to record run ({exc}).")


def derive_status(results):
    """success = all ok (or nothing to do); failed = all failed; else partial."""
    if not results:
        return "success"
    failed = sum(1 for r in results if r.get("error") or r.get("s3_key") == "FAILED")
    if failed == 0:
        return "success"
    if failed == len(results):
        return "failed"
    return "partial"


# =============================================================================
# STEP 1: Get all collection names from Qdrant
# =============================================================================

def get_all_collections():
    """Fetch the list of ALL collections from the Qdrant instance."""
    log("📂 Fetching list of all collections from Qdrant...")

    url = f"{QDRANT_URL}/collections"
    headers = {"api-key": QDRANT_API_KEY}

    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    collections = [c["name"] for c in data["result"]["collections"]]

    log(f"   Found {len(collections)} collection(s): {', '.join(collections)}")
    return collections


# =============================================================================
# STEP 2: Create a snapshot for a collection
# =============================================================================

def create_snapshot(collection_name):
    """Trigger Qdrant to create a snapshot for the given collection."""
    log(f"📸 Creating snapshot for collection: '{collection_name}'...")

    url = f"{QDRANT_URL}/collections/{collection_name}/snapshots"
    headers = {"api-key": QDRANT_API_KEY}

    response = requests.post(url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    snapshot_info = response.json()["result"]
    snapshot_name = snapshot_info["name"]
    snapshot_size = snapshot_info.get("size", "unknown")

    log(f"   ✅ Snapshot created: {snapshot_name} (size: {snapshot_size})")
    return snapshot_name


# =============================================================================
# STEP 3: Download the snapshot file from Qdrant
# =============================================================================

def download_snapshot(collection_name, snapshot_name):
    """Download the snapshot file from Qdrant server (streamed)."""
    log(f"⬇️  Downloading snapshot '{snapshot_name}' from Qdrant...")

    url = f"{QDRANT_URL}/collections/{collection_name}/snapshots/{snapshot_name}"
    headers = {"api-key": QDRANT_API_KEY}

    response = requests.get(url, headers=headers, stream=True, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    local_path = f"/tmp/{snapshot_name}"
    total_bytes = 0

    with open(local_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            total_bytes += len(chunk)

    size_mb = total_bytes / (1024 * 1024)
    log(f"   ✅ Downloaded {size_mb:.2f} MB to {local_path}")
    return local_path


# =============================================================================
# STEP 4: Upload the snapshot to Cloudflare R2
# =============================================================================

def upload_to_r2(local_path, collection_name):
    """Upload the snapshot file to Cloudflare R2 (S3-compatible)."""
    today = datetime.now().strftime("%Y-%m-%d")
    s3_key = f"{R2_BACKUP_PREFIX}/{today}/{collection_name}.snapshot"

    log(f"☁️  Uploading to R2: {s3_key}...")

    s3_client = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )

    s3_client.upload_file(local_path, R2_BUCKET_NAME, s3_key)

    log(f"   ✅ Uploaded successfully to: s3://{R2_BUCKET_NAME}/{s3_key}")
    return s3_key


# =============================================================================
# STEP 5: Delete the snapshot from Qdrant server (cleanup)
# =============================================================================

def delete_qdrant_snapshot(collection_name, snapshot_name):
    """Remove the snapshot file from the Qdrant server to free disk space."""
    log(f"🗑️  Cleaning up snapshot from Qdrant server: {snapshot_name}...")

    url = f"{QDRANT_URL}/collections/{collection_name}/snapshots/{snapshot_name}"
    headers = {"api-key": QDRANT_API_KEY}

    response = requests.delete(url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    log(f"   ✅ Snapshot removed from Qdrant server")


# =============================================================================
# STEP 6: Clean up old backups from R2 (retention policy)
# =============================================================================

def cleanup_old_backups():
    """Delete backup folders in R2 older than RETENTION_DAYS. Returns the count."""
    log(f"🧹 Cleaning up backups older than {RETENTION_DAYS} days from R2...")

    s3_client = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    log(f"   Cutoff date: {cutoff_date.strftime('%Y-%m-%d')}")

    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=f"{R2_BACKUP_PREFIX}/")

    objects_to_delete = []
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            parts = key.split("/")
            if len(parts) >= 2:
                date_str = parts[1]
                try:
                    backup_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if backup_date < cutoff_date:
                        objects_to_delete.append({"Key": key})
                except ValueError:
                    continue

    if objects_to_delete:
        for i in range(0, len(objects_to_delete), 1000):
            batch = objects_to_delete[i:i + 1000]
            s3_client.delete_objects(Bucket=R2_BUCKET_NAME, Delete={"Objects": batch})
        log(f"   ✅ Deleted {len(objects_to_delete)} old backup file(s)")
    else:
        log(f"   ✅ No old backups to clean up")

    return len(objects_to_delete)


# =============================================================================
# STEP 7: Remove local temp file
# =============================================================================

def cleanup_local_file(local_path):
    """Remove the temporary local snapshot file after upload."""
    try:
        os.remove(local_path)
        log(f"   🗑️  Removed local temp file: {local_path}")
    except OSError:
        log(f"   ⚠️  Could not remove temp file: {local_path}")


# =============================================================================
# STEP 8: Send email report via Resend
# =============================================================================

def send_email_report(results, total_time, error=None):
    """Send an HTML email report summarizing the backup results."""
    log("📧 Sending email report via Resend...")

    resend.api_key = RESEND_API_KEY
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    if error:
        subject = f"❌ Qdrant Backup FAILED — {today}"
        html = f"""
        <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #dc2626, #991b1b); padding: 24px; border-radius: 12px 12px 0 0;">
                <h1 style="color: white; margin: 0; font-size: 22px;">❌ Qdrant Backup Failed</h1>
                <p style="color: #fecaca; margin: 8px 0 0 0; font-size: 14px;">{today}</p>
            </div>
            <div style="background: #1a1a2e; padding: 24px; border-radius: 0 0 12px 12px; color: #e2e8f0;">
                <div style="background: #7f1d1d; border-left: 4px solid #ef4444; padding: 16px; border-radius: 8px; margin-bottom: 16px;">
                    <p style="margin: 0; color: #fca5a5; font-weight: 600;">Error Details:</p>
                    <p style="margin: 8px 0 0 0; color: #fecaca; font-family: monospace; font-size: 13px;">{str(error)}</p>
                </div>
                <p style="color: #94a3b8; font-size: 13px; margin: 16px 0 0 0;">
                    Please check the script logs in PyRunner for more details.
                </p>
            </div>
        </div>
        """
    else:
        subject = f"✅ Qdrant Backup Complete — {today}"

        rows = ""
        total_size = 0
        for r in results:
            size_mb = r["size_mb"]
            total_size += size_mb
            status_badge = '<span style="background: #065f46; color: #6ee7b7; padding: 2px 8px; border-radius: 4px; font-size: 12px;">✅ OK</span>'
            rows += f"""
            <tr style="border-bottom: 1px solid #2d3748;">
                <td style="padding: 12px; color: #e2e8f0;">{r['collection']}</td>
                <td style="padding: 12px; color: #94a3b8;">{size_mb:.2f} MB</td>
                <td style="padding: 12px;">{status_badge}</td>
            </tr>
            """

        html = f"""
        <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #059669, #047857); padding: 24px; border-radius: 12px 12px 0 0;">
                <h1 style="color: white; margin: 0; font-size: 22px;">✅ Qdrant Backup Complete</h1>
                <p style="color: #a7f3d0; margin: 8px 0 0 0; font-size: 14px;">{today}</p>
            </div>
            <div style="background: #1a1a2e; padding: 24px; border-radius: 0 0 12px 12px; color: #e2e8f0;">
                <div style="display: flex; gap: 12px; margin-bottom: 20px;">
                    <div style="flex: 1; background: #16213e; padding: 16px; border-radius: 8px; text-align: center;">
                        <div style="font-size: 24px; font-weight: bold; color: #6ee7b7;">{len(results)}</div>
                        <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">Collections</div>
                    </div>
                    <div style="flex: 1; background: #16213e; padding: 16px; border-radius: 8px; text-align: center;">
                        <div style="font-size: 24px; font-weight: bold; color: #6ee7b7;">{total_size:.1f} MB</div>
                        <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">Total Size</div>
                    </div>
                    <div style="flex: 1; background: #16213e; padding: 16px; border-radius: 8px; text-align: center;">
                        <div style="font-size: 24px; font-weight: bold; color: #6ee7b7;">{total_time:.0f}s</div>
                        <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">Duration</div>
                    </div>
                </div>

                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="border-bottom: 2px solid #2d3748;">
                            <th style="padding: 12px; text-align: left; color: #94a3b8; font-size: 13px;">Collection</th>
                            <th style="padding: 12px; text-align: left; color: #94a3b8; font-size: 13px;">Size</th>
                            <th style="padding: 12px; text-align: left; color: #94a3b8; font-size: 13px;">Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>

                <p style="color: #64748b; font-size: 12px; margin-top: 20px; text-align: center;">
                    Backups stored in R2: <code>{R2_BACKUP_PREFIX}/</code> · Retention: {RETENTION_DAYS} days
                </p>
            </div>
        </div>
        """

    resend.Emails.send({
        "from": EMAIL_FROM,
        "to": [EMAIL_TO],
        "subject": subject,
        "html": html,
    })

    log(f"   ✅ Email report sent to {EMAIL_TO}")


# =============================================================================
# MAIN: Orchestrate the full backup pipeline
# =============================================================================

def main():
    log("=" * 60)
    log("🚀 QDRANT BACKUP TO CLOUDFLARE R2 — Starting...")
    log("=" * 60)
    log(f"   Qdrant URL:   {QDRANT_URL}")
    log(f"   R2 Bucket:    {R2_BUCKET_NAME}")
    log(f"   Retention:    {RETENTION_DAYS} days")
    log("")

    start_time = time.time()
    results = []

    try:
        # ---- Step 1: Get all collections ----
        collections = get_all_collections()

        if not collections:
            log("⚠️  No collections found in Qdrant. Nothing to back up.")
            total_time = time.time() - start_time
            record_run("success", results, total_time, deleted_old=0)
            send_email_report(results, total_time)
            return

        # ---- Step 2: Backup each collection ----
        for i, collection_name in enumerate(collections, 1):
            log("")
            log(f"{'─' * 40}")
            log(f"📦 Processing collection {i}/{len(collections)}: '{collection_name}'")
            log(f"{'─' * 40}")

            try:
                snapshot_name = create_snapshot(collection_name)
                local_path = download_snapshot(collection_name, snapshot_name)
                file_size_mb = os.path.getsize(local_path) / (1024 * 1024)
                s3_key = upload_to_r2(local_path, collection_name)
                delete_qdrant_snapshot(collection_name, snapshot_name)
                cleanup_local_file(local_path)

                results.append({
                    "collection": collection_name,
                    "size_mb": file_size_mb,
                    "s3_key": s3_key,
                })
                log(f"   ✅ Collection '{collection_name}' backed up successfully!")

            except Exception as e:
                log(f"   ❌ ERROR backing up '{collection_name}': {str(e)}")
                results.append({
                    "collection": collection_name,
                    "size_mb": 0,
                    "s3_key": "FAILED",
                    "error": str(e),
                })

            if i < len(collections):
                time.sleep(2)

        # ---- Step 3: Clean up old backups from R2 ----
        log("")
        deleted_count = cleanup_old_backups()

        # ---- Step 4: Record to dashboard + send email report ----
        log("")
        total_time = time.time() - start_time
        status = derive_status(results)
        record_run(status, results, total_time, deleted_old=deleted_count)
        send_email_report(results, total_time)

        # ---- Final Summary ----
        log("")
        log("=" * 60)
        log(f"🏁 BACKUP COMPLETE!")
        log(f"   Status:                {status}")
        log(f"   Collections backed up: {sum(1 for r in results if r.get('s3_key') != 'FAILED' and not r.get('error'))}")
        log(f"   Old files cleaned up:  {deleted_count}")
        log(f"   Total time:            {total_time:.1f} seconds")
        log("=" * 60)

    except Exception as e:
        log(f"💥 CRITICAL ERROR: {str(e)}")
        total_time = time.time() - start_time
        record_run("failed", results, total_time, deleted_old=0, error=e)
        send_email_report(results, total_time, error=e)
        sys.exit(1)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    main()
