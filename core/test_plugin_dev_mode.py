"""
Plugin Platform v2 — Stage 1 (Dev Mode, WS1) tests.

Dev Mode loads ONE plugin from a local folder under ``manage.py runserver`` so a
developer iterates live (StatReloader reloads .py/templates) with no zip/upload/
preflight/restart. The two load-bearing pieces are:

  1. ``PluginService.validate_dev_mode_plugin`` — the structural gate (also used
     by the future plugin_doctor --path).
  2. The settings mechanism: splice the dev folder's parent onto the ``plugins``
     package ``__path__`` so ``plugins.<slug>`` resolves to it, making the dev
     form byte-identical to the shipped form (apps.py: name="plugins.<slug>").

These tests exercise both without a running server. They never need the DB, so
they use SimpleTestCase.
"""

import importlib
import sys
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from core.services.plugin_service import PluginService, PluginInstallError

APPS_PY = '''\
from core.plugins import NavItem, PluginAppConfig, PyRunnerPlugin


class {cls}(PluginAppConfig):
    name = "plugins.{slug}"
    label = "{slug}"
    plugin = PyRunnerPlugin(slug="{slug}", name="Dev Fixture", version="0.0.1")
'''

URLS_PY = '''\
from django.urls import path

app_name = "{slug}"
urlpatterns = [path("", lambda r: None, name="index")]
'''


class _DevPluginFixture:
    """Build a throwaway plugin folder on disk and clean up importer state."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pyrunner-devplugin-")
        self.root = Path(self._tmp.name)

    def make(self, slug, *, with_init=True, with_apps=True,
             with_models=False, with_migrations=False):
        folder = self.root / slug
        folder.mkdir(parents=True, exist_ok=True)
        if with_init:
            (folder / "__init__.py").write_text("", encoding="utf-8")
        if with_apps:
            cls = "".join(p.capitalize() for p in slug.split("_")) + "Config"
            (folder / "apps.py").write_text(
                APPS_PY.format(cls=cls, slug=slug), encoding="utf-8"
            )
            (folder / "urls.py").write_text(URLS_PY.format(slug=slug), encoding="utf-8")
        if with_models:
            (folder / "models.py").write_text(
                "from django.db import models\n", encoding="utf-8"
            )
        if with_migrations:
            mig = folder / "migrations"
            mig.mkdir(exist_ok=True)
            (mig / "__init__.py").write_text("", encoding="utf-8")
        return folder

    def cleanup(self):
        self._tmp.cleanup()


class ValidateDevModePluginTests(SimpleTestCase):
    def setUp(self):
        self.fix = _DevPluginFixture()
        self.addCleanup(self.fix.cleanup)

    def test_valid_plugin_returns_slug_and_no_warnings(self):
        folder = self.fix.make("devmode_fixture")
        slug, warnings = PluginService.validate_dev_mode_plugin(folder)
        self.assertEqual(slug, "devmode_fixture")
        self.assertEqual(warnings, [])

    def test_missing_apps_py_raises(self):
        folder = self.fix.make("devmode_fixture", with_apps=False)
        with self.assertRaises(PluginInstallError):
            PluginService.validate_dev_mode_plugin(folder)

    def test_missing_init_py_raises(self):
        folder = self.fix.make("devmode_fixture", with_init=False)
        with self.assertRaises(PluginInstallError):
            PluginService.validate_dev_mode_plugin(folder)

    def test_invalid_slug_from_folder_name_raises(self):
        folder = self.fix.make("Bad-Slug")
        with self.assertRaises(PluginInstallError):
            PluginService.validate_dev_mode_plugin(folder)

    def test_nonexistent_path_raises(self):
        with self.assertRaises(PluginInstallError):
            PluginService.validate_dev_mode_plugin(self.fix.root / "does_not_exist")

    def test_models_and_migrations_warn_but_do_not_fail(self):
        folder = self.fix.make(
            "devmode_fixture", with_models=True, with_migrations=True
        )
        slug, warnings = PluginService.validate_dev_mode_plugin(folder)
        self.assertEqual(slug, "devmode_fixture")
        # One warning each for models.py and migrations/.
        self.assertEqual(len(warnings), 2)
        joined = " ".join(warnings).lower()
        self.assertIn("models.py", joined)
        self.assertIn("migrations", joined)


class DevModePathSpliceTests(SimpleTestCase):
    """The settings mechanism: a spliced plugins.__path__ makes the external dev
    folder importable as plugins.<slug>, byte-identical to a shipped plugin."""

    SLUG = "devmode_splice_fixture"

    def setUp(self):
        self.fix = _DevPluginFixture()
        self.addCleanup(self.fix.cleanup)
        import plugins as plugins_pkg

        self._plugins_pkg = plugins_pkg
        self._orig_path = list(plugins_pkg.__path__)
        self.addCleanup(self._restore)

    def _restore(self):
        # Drop any modules we imported and restore the package search path so the
        # fixture can't leak into other tests.
        for name in list(sys.modules):
            if name == f"plugins.{self.SLUG}" or name.startswith(
                f"plugins.{self.SLUG}."
            ):
                sys.modules.pop(name, None)
        self._plugins_pkg.__path__[:] = self._orig_path

    def _splice(self, folder):
        parent = str(folder.parent)
        if parent not in self._plugins_pkg.__path__:
            self._plugins_pkg.__path__.append(parent)

    def test_external_folder_imports_as_plugins_slug(self):
        folder = self.fix.make(self.SLUG)
        self._splice(folder)

        mod = importlib.import_module(f"plugins.{self.SLUG}.apps")
        # The AppConfig declares name="plugins.<slug>" — proving the dev form is
        # identical to the shipped form (no apps.py edits between dev and ship).
        cfg = next(
            obj
            for obj in vars(mod).values()
            if isinstance(obj, type)
            and getattr(obj, "name", None) == f"plugins.{self.SLUG}"
        )
        self.assertEqual(cfg.label, self.SLUG)

    def test_light_import_ok_accepts_spliced_dev_plugin(self):
        # Use the ACTUAL settings guard the dev block relies on.
        import pyrunner.settings as st

        folder = self.fix.make(self.SLUG)
        self._splice(folder)

        ok, err = st._light_import_ok(self.SLUG)
        self.assertTrue(ok, err)
        self.assertEqual(err, "")
