"""
plugin_uninstall — drop a plugin's own DB tables by unapplying its migrations.

This is the "also remove data" path of plugin deletion. It MUST be run in
isolation (env PYRUNNER_PREFLIGHT_SLUG=<slug>, so settings loads only this one
plugin) — the PluginService spawns it that way. It runs `migrate <label> zero`,
which reverses the plugin's migrations and drops the tables it created. A broken
plugin can't be migrated, in which case this exits non-zero and the caller still
removes the files/row (with a warning).
"""

import importlib.util

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Drop a plugin's DB tables (migrate <label> zero). Run isolated."

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Plugin slug to uninstall data for.")

    def handle(self, *args, **options):
        slug = options["slug"]
        app_name = f"plugins.{slug}"

        app_config = next(
            (c for c in apps.get_app_configs() if c.name == app_name), None
        )
        if app_config is None:
            raise CommandError(
                f"Plugin app '{app_name}' is not loaded — run with PYRUNNER_PREFLIGHT_SLUG."
            )

        if importlib.util.find_spec(f"{app_name}.migrations") is None:
            self.stdout.write(f"Plugin '{slug}' has no migrations; no data to drop.")
            return

        call_command("migrate", app_config.label, "zero", verbosity=0, interactive=False)
        self.stdout.write(self.style.SUCCESS(f"Dropped tables for plugin '{slug}'."))
