"""
Seed the bundled example plugin on first boot.

A fresh deployment starts with an empty plugins volume, so nothing shows in the
Plugins page. To give operators a ready example to look at, try, or delete, this
copies ``examples/sales_dashboard`` (baked into the image) onto the plugins
volume and creates an INSTALLED (NOT active) Plugin row.

Safety:
  * Seeds as INSTALLED, never active — it is never loaded until a superuser
    explicitly activates it (installed != active).
  * Runs once, guarded by a sentinel file on the plugins volume. If the operator
    later deletes the example, it is NOT re-seeded on the next boot.
  * Fully wrapped so it can never break container start.
"""

import json
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

EXAMPLE_SLUG = "sales_dashboard"
SENTINEL_NAME = ".example_seeded"


class Command(BaseCommand):
    help = "Seed the bundled example plugin on first boot (idempotent, never active)."

    def handle(self, *args, **options):
        try:
            self._seed()
        except Exception as exc:  # never break boot
            self.stderr.write(f"Example plugin seed skipped: {exc!r}")

    def _seed(self):
        plugins_dir = Path(settings.PLUGINS_DIR)
        sentinel = plugins_dir / SENTINEL_NAME
        if sentinel.exists():
            return  # already seeded once — respect any later deletion

        source = Path(settings.BASE_DIR) / "examples" / EXAMPLE_SLUG
        if not source.is_dir():
            # No bundled example in this image; retry on a future boot if added.
            self.stdout.write("No bundled example plugin to seed.")
            return

        from core.models import Plugin

        dest = plugins_dir / EXAMPLE_SLUG
        if Plugin.objects.filter(slug=EXAMPLE_SLUG).exists() or dest.exists():
            # Operator already has something by this slug — don't touch it.
            self.stdout.write("Example plugin already present; not seeding.")
        else:
            plugins_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                source,
                dest,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

            manifest = {}
            manifest_path = dest / "plugin.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    manifest = {}

            Plugin.objects.create(
                slug=EXAMPLE_SLUG,
                name=manifest.get("name") or "Sales Dashboard",
                version=str(manifest.get("version") or "1.0.0"),
                status=Plugin.Status.INSTALLED,
                source=Plugin.Source.BUILTIN,
                manifest=manifest,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Seeded example plugin '{EXAMPLE_SLUG}' (status=INSTALLED, not active)."
                )
            )

        # Mark as seeded so we never re-seed (even after the operator deletes it).
        sentinel.write_text("seeded\n", encoding="utf-8")
