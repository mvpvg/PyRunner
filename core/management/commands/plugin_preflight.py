"""
plugin_preflight — validate plugin(s) in isolation.

The whole safety model rests on this command: a plugin is only ever loaded into
the live server *after* it has passed an import + migrate + URL-resolution check
in a separate, throwaway process. If that check fails, the live server is never
touched (the failure happened somewhere else).

Two modes:

    plugin_preflight <slug>
        Validate one plugin in an ISOLATED subprocess. If not already running as
        the isolated child, it re-execs itself with PYRUNNER_PREFLIGHT_SLUG set
        so settings loads ONLY that plugin (unguarded). Exit 0 = pass, non-zero
        = fail (diagnostic on stderr). Writes nothing to the DB — the caller
        (e.g. activation) decides what to do with the result.

    plugin_preflight --all [--disable-broken]
        Validate every ACTIVE plugin, each in its own subprocess. With
        --disable-broken, any plugin that fails is flipped to ERRORED so the
        next boot won't load it. ALWAYS exits 0 — it must never abort container
        start. Run it with PYRUNNER_DISABLE_PLUGINS=1 so this orchestrator
        process itself boots clean even if an active plugin is currently broken.
"""

import importlib
import importlib.util
import os
import subprocess
import sys
import traceback

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# These mirror the env vars the settings loader and the AppConfig base react to.
PREFLIGHT_SLUG_ENV = "PYRUNNER_PREFLIGHT_SLUG"
PREFLIGHT_FLAG_ENV = "PYRUNNER_PLUGIN_PREFLIGHT"
DISABLE_ENV = "PYRUNNER_DISABLE_PLUGINS"

# A hanging plugin import must not hang the container boot forever.
CHILD_TIMEOUT = 120  # seconds


class Command(BaseCommand):
    help = "Validate plugin(s) in an isolated process before they are loaded live."

    def add_arguments(self, parser):
        parser.add_argument("slug", nargs="?", help="Plugin slug to validate.")
        parser.add_argument(
            "--all",
            action="store_true",
            help="Validate every ACTIVE plugin, each in its own subprocess.",
        )
        parser.add_argument(
            "--disable-broken",
            action="store_true",
            help="With --all: flip any failing plugin to ERRORED.",
        )

    def handle(self, *args, **options):
        if options["all"]:
            self._run_all(disable_broken=options["disable_broken"])
            return

        slug = options["slug"]
        if not slug:
            raise CommandError("Provide a plugin slug, or use --all.")

        if os.environ.get(PREFLIGHT_SLUG_ENV) == slug:
            # We ARE the isolated child: settings has already loaded only this
            # plugin (unguarded) and django.setup() imported its apps + models
            # (or this process would have died before reaching handle()).
            ok, message = self._validate_loaded(slug)
            if ok:
                self.stdout.write(self.style.SUCCESS(f"Plugin '{slug}' passed preflight."))
            else:
                self.stderr.write(message)
                raise SystemExit(1)
        else:
            # Re-exec in isolation so a broken import cannot affect this process.
            code, output = self._spawn_child(slug)
            if output:
                self.stdout.write(output.rstrip())
            if code != 0:
                raise SystemExit(code)

    # -- validation (runs inside the isolated child) -----------------------

    def _validate_loaded(self, slug):
        """Validate the already-loaded plugin: migrate + resolve its URLs.

        Import errors in apps/models/__init__ have, by this point, either passed
        during django.setup() or already crashed this isolated process — which
        is exactly the isolation we want. Here we only exercise what setup() does
        NOT: applying migrations and importing the plugin's urls.
        """
        from django.apps import apps
        from django.core.management import call_command

        app_name = f"plugins.{slug}"
        app_config = next(
            (c for c in apps.get_app_configs() if c.name == app_name), None
        )
        if app_config is None:
            return False, f"Plugin app '{app_name}' is not installed."

        # 1) Migrations — apply this plugin's migrations against the real DB.
        #    Only attempt it if the plugin actually ships a migrations package;
        #    `migrate <label>` errors for an app that has none.
        if importlib.util.find_spec(f"{app_name}.migrations") is not None:
            try:
                call_command(
                    "migrate", app_config.label, verbosity=0, interactive=False
                )
            except Exception:
                return False, "Migration failed:\n" + traceback.format_exc()

        # 2) URL resolution — import the plugin's urls (if any), unguarded.
        try:
            importlib.import_module(f"{app_name}.urls")
        except ModuleNotFoundError as exc:
            # urls.py simply not present -> fine (plugin has no routes). But a
            # urls.py that imports something missing IS a failure.
            if exc.name not in (f"{app_name}.urls",):
                return False, "URL import failed:\n" + traceback.format_exc()
        except Exception:
            return False, "URL import failed:\n" + traceback.format_exc()

        # 3) Light-import guard (Plugin Platform v2): importing the plugin's apps
        #    module must NOT transitively import core.models. The settings-time
        #    light-import pre-check runs BEFORE the app registry is ready, so a
        #    top-level `from core.models import ...` would crash the boot loader.
        #    Plugins must import core lazily inside functions, or use the SDK
        #    (core.plugins.api), which is import-light by design.
        ok, message = self._assert_light_import(slug)
        if not ok:
            return False, message

        return True, ""

    def _assert_light_import(self, slug):
        """Check (in a clean process) that importing plugins.<slug>.apps does not
        drag in core.models. Returns (ok, message)."""
        code = (
            "import sys\n"
            f"import plugins.{slug}.apps\n"
            "sys.exit(7 if 'core.models' in sys.modules else 0)\n"
        )
        env = os.environ.copy()
        env.pop(PREFLIGHT_SLUG_ENV, None)  # plain `-c` import; don't run the loader
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=str(settings.BASE_DIR),
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return False, "Light-import check timed out."
        if proc.returncode == 7:
            return False, (
                f"Plugin '{slug}': apps.py transitively imports core.models, which "
                "would break the light-import boot guard. Import core lazily inside "
                "functions, or use the SDK (core.plugins.api)."
            )
        if proc.returncode != 0:
            return False, (
                "Light-import check failed:\n" + (proc.stdout + proc.stderr).strip()
            )
        return True, ""

    # -- orchestration ------------------------------------------------------

    def _child_env(self, slug):
        env = os.environ.copy()
        env.pop(DISABLE_ENV, None)  # the child MUST load the plugin
        env[PREFLIGHT_SLUG_ENV] = slug
        env[PREFLIGHT_FLAG_ENV] = "1"  # make a broken ready() re-raise
        return env

    def _spawn_child(self, slug):
        manage_py = str(settings.BASE_DIR / "manage.py")
        cmd = [sys.executable, manage_py, "plugin_preflight", slug]
        try:
            proc = subprocess.run(
                cmd,
                env=self._child_env(slug),
                capture_output=True,
                text=True,
                timeout=CHILD_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return 1, f"Preflight timed out after {CHILD_TIMEOUT}s."
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, output

    def _run_all(self, disable_broken):
        from core.models import Plugin

        active = list(Plugin.objects.filter(status=Plugin.Status.ACTIVE))
        if not active:
            self.stdout.write("No active plugins to preflight.")
            return

        passed, failed = [], []
        for plugin in active:
            code, output = self._spawn_child(plugin.slug)
            if code == 0:
                passed.append(plugin.slug)
                self.stdout.write(self.style.SUCCESS(f"  ok    {plugin.slug}"))
            else:
                failed.append(plugin.slug)
                self.stdout.write(self.style.ERROR(f"  FAIL  {plugin.slug}"))
                if disable_broken:
                    plugin.mark_errored(output.strip()[:4000] or "Preflight failed.")

        self.stdout.write("")
        summary = f"Preflight: {len(passed)} passed, {len(failed)} failed."
        if failed and disable_broken:
            summary += " Broken plugins quarantined (ERRORED)."
        self.stdout.write(summary)
        # Never exit non-zero: container boot must continue regardless.
