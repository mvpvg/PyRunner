"""
Release notes shown in-app at /cpanel/changelog/.

Keep the newest release first. Each entry groups its changes by a short tag
("Added" / "Improved" / "Fixed" / "Security"); the template maps tags to colors.
When you cut a release, bump ``pyrunner/version.py`` and add an entry here.
"""

CHANGELOG = [
    {
        "version": "1.11.0",
        "date": "June 21, 2026",
        "headline": (
            "Plugin Platform v2 — develop plugins live, build them on a stable "
            "SDK, and let them own their own scripts, secrets, and data with no "
            "risk to your core database."
        ),
        "changes": [
            {
                "tag": "Added",
                "title": "Plugin Dev Mode",
                "body": (
                    "Point PYRUNNER_PLUGIN_DEV at a local plugin folder under "
                    "`manage.py runserver` and edit it live — code and templates "
                    "reload instantly, with no upload, preflight, or restart. "
                    "Triple-guarded so it never loads on a production server."
                ),
            },
            {
                "tag": "Added",
                "title": "Plugin SDK (core.plugins.api)",
                "body": (
                    "Plugins now orchestrate scripts, secrets, datastores, "
                    "schedules, and runs through a stable, versioned SDK instead of "
                    "reaching into core internals. It auto-stamps ownership + the "
                    "workspace, is idempotent (re-saving config updates the same "
                    "rows, never duplicates), and runs everything through the real "
                    "execution + scheduling paths."
                ),
            },
            {
                "tag": "Added",
                "title": "Resource ownership + scoped secrets",
                "body": (
                    "Scripts, secrets, and datastores a plugin creates are grouped "
                    "under it with an owner pill, delete-guarded on the generic "
                    "pages (so you can't pull the rug out from under a plugin), and "
                    "removed cleanly on uninstall. Secrets can be injected per "
                    "script (Selected mode) instead of all-at-once — fully opt-in, "
                    "so existing scripts are unchanged."
                ),
            },
            {
                "tag": "Added",
                "title": "Plugin doctor",
                "body": (
                    "Activation now runs a static-lint 'doctor' that refuses a "
                    "rule-breaking plugin with a clear per-rule report — before any "
                    "of its code or migrations can run. Plugins ship no database "
                    "models, so no plugin can ever alter a core table. Authors can "
                    "run `manage.py plugin_doctor` on a folder before shipping."
                ),
            },
            {
                "tag": "Security",
                "title": "No plugin DDL reaches the database",
                "body": (
                    "Plugins persist via owned key-value DataStores, not their own "
                    "tables. With no plugin models or migrations, the entire class "
                    "of 'a plugin migration broke the database' is removed by "
                    "construction — verified live against Postgres."
                ),
            },
        ],
    },
    {
        "version": "1.10.0",
        "date": "June 15, 2026",
        "headline": (
            "Plugins — extend PyRunner with self-contained apps you upload, "
            "validate, and activate, with a hard guarantee that a broken plugin "
            "can never take down your site."
        ),
        "changes": [
            {
                "tag": "Added",
                "title": "Plugin system",
                "body": (
                    "Install plugins from the console under Plugins (superuser "
                    "only): upload a .zip, then Activate to validate and enable it. "
                    "A plugin is a self-contained Django app that adds pages to the "
                    "sidebar and serves at /plugins/<slug>/ — no core edits, no "
                    "fork. See docs/plugins.md and examples/example_plugin for how "
                    "to write one."
                ),
            },
            {
                "tag": "Added",
                "title": "Safe by design — a broken plugin can't break the site",
                "body": (
                    "Installed is not active: uploading never imports plugin code. "
                    "Activation validates the plugin in a throwaway subprocess "
                    "first, and every boot re-checks each active plugin in "
                    "isolation, auto-quarantining any that fail. Plugin pages and "
                    "compute are sandboxed, and a kill switch "
                    "(PYRUNNER_DISABLE_PLUGINS=1) guarantees a clean recovery boot."
                ),
            },
            {
                "tag": "Added",
                "title": "Run plugin compute in an environment",
                "body": (
                    "Plugins run heavy or third-party-dependent work inside a "
                    "chosen environment's venv via run_in_environment — an isolated "
                    "subprocess with a timeout and captured output — so extra "
                    "packages never touch the main app."
                ),
            },
        ],
    },
    {
        "version": "1.9.0",
        "date": "June 14, 2026",
        "headline": (
            "A redesigned console, full mobile support, Claude AI in your "
            "scripts, and real control over running jobs."
        ),
        "changes": [
            {
                "tag": "Added",
                "title": "Claude AI integration",
                "body": (
                    "Connect Claude under Services → Claude AI and call it "
                    "straight from your scripts with the bundled pyrunner_ai "
                    "helper. Token usage is tracked per run and per script so you "
                    "can see exactly what each automation costs."
                ),
            },
            {
                "tag": "Added",
                "title": "Force stop running jobs",
                "body": (
                    "Stop a running script for real — the Stop button now "
                    "kills the script's whole process tree (and any child "
                    "processes it spawned), instead of just marking it cancelled "
                    "and waiting for the timeout. The background worker is never "
                    "touched, so the queue keeps processing the moment a job is "
                    "stopped. Stop is available from the Tasks page and the Run "
                    "detail page."
                ),
            },
            {
                "tag": "Added",
                "title": "Per-task detail pages",
                "body": (
                    "Every task is now clickable — drill into queued, "
                    "completed, failed, and system tasks to see the function, "
                    "arguments, result, timing, live PID, and full tracebacks "
                    "for failures. The Run detail page gained task metadata and a "
                    "live PID while running."
                ),
            },
            {
                "tag": "Improved",
                "title": "Redesigned console",
                "body": (
                    "The entire control panel and the authentication screens "
                    "have been rebuilt on a new, consistent design system — "
                    "cleaner panels, clearer status, a refined type scale, and a "
                    "cohesive color and spacing language throughout."
                ),
            },
            {
                "tag": "Improved",
                "title": "Mobile responsive layout",
                "body": (
                    "PyRunner now works on phones and tablets. The sidebar "
                    "becomes an off-canvas drawer, and tables, editors, and forms "
                    "reflow to fit small screens so you can manage runs on the go."
                ),
            },
        ],
    },
    {
        "version": "1.8.2",
        "date": "Earlier release",
        "headline": "",
        "changes": [
            {
                "tag": "Added",
                "title": "Admin-configured auth emails",
                "body": "Authentication emails can be configured by an admin.",
            },
            {
                "tag": "Improved",
                "title": "Longer default timeout",
                "body": "Raised the default script execution timeout.",
            },
        ],
    },
    {
        "version": "1.8.1",
        "date": "Earlier release",
        "headline": "",
        "changes": [
            {
                "tag": "Security",
                "title": "S3 hardening",
                "body": "Security improvements for S3 storage and backups.",
            },
        ],
    },
]
