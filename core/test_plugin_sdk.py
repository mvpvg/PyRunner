"""
Plugin Platform v2 — Stage 3 (SDK facade, WS2) tests.

Exercises core.plugins.api: idempotent owner-keyed upserts, ownership + workspace
auto-stamping, DataStore auto-naming, the legacy (owner=None) lane, bulk
set_environment, and that the SDK is import-light (no core.models at module top).
"""

import subprocess
import sys

from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from core.plugins.api import (
    API_VERSION,
    DataStoreAPI,
    EnvironmentAPI,
    ScheduleAPI,
    ScriptAPI,
    SecretAPI,
)
from core.models import (
    DataStore,
    Environment,
    Run,
    Script,
    ScriptSchedule,
    Secret,
    SecretGrant,
    Workspace,
)


class LightImportTests(SimpleTestCase):
    def test_api_version_present(self):
        self.assertTrue(API_VERSION)

    def test_sdk_does_not_import_core_models_at_module_top(self):
        # The whole point: a plugin's apps.py can `from core.plugins.api import …`
        # without dragging in core.models (keeps the light-import boot guard).
        code = "import sys; import core.plugins.api; sys.exit(7 if 'core.models' in sys.modules else 0)"
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(settings.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class ScriptAPITests(TestCase):
    def setUp(self):
        self.ws = Workspace.get_default()
        self.env = Environment.objects.create(name="e", path="sdkenv")
        self.env2 = Environment.objects.create(name="e2", path="sdkenv2")
        self.api = ScriptAPI("myplugin")

    def test_upsert_is_idempotent_and_stamps_owner_workspace(self):
        s1 = self.api.upsert(key="backup", code="print(1)", environment=self.env)
        s2 = self.api.upsert(key="backup", code="print(2)", environment=self.env)
        self.assertEqual(s1.pk, s2.pk)  # same row updated, not duplicated
        self.assertEqual(Script.objects.filter(owner_plugin="myplugin").count(), 1)
        self.assertEqual(s2.owner_plugin, "myplugin")
        self.assertEqual(s2.owner_key, "backup")
        self.assertEqual(s2.workspace_id, self.ws.id)
        self.assertEqual(s2.code, "print(2)")
        # Owned scripts default to selected injection; isolation left to the policy.
        self.assertEqual(s2.injection_mode, Script.InjectionMode.SELECTED)
        self.assertEqual(s2.isolation_mode, Script.IsolationMode.INHERIT)

    def test_name_auto_derived_when_omitted(self):
        s = self.api.upsert(key="restore", code="x", environment=self.env)
        self.assertEqual(s.name, "myplugin:restore")

    def test_upsert_requires_key_for_owned(self):
        with self.assertRaises(ValueError):
            self.api.upsert(code="x", environment=self.env)

    def test_upsert_requires_environment_on_create(self):
        with self.assertRaises(ValueError):
            self.api.upsert(key="k", code="x")

    def test_environment_accepts_name_string(self):
        s = self.api.upsert(key="k", code="x", environment="e")
        self.assertEqual(s.environment_id, self.env.id)

    def test_set_environment_bulk_updates_all_owned(self):
        self.api.upsert(key="a", code="x", environment=self.env)
        self.api.upsert(key="b", code="y", environment=self.env)
        # A user script must NOT be touched.
        user_script = Script.objects.create(
            name="u", code="z", environment=self.env, workspace=self.ws
        )
        n = self.api.set_environment(self.env2)
        self.assertEqual(n, 2)
        self.assertTrue(
            all(s.environment_id == self.env2.id
                for s in Script.objects.filter(owner_plugin="myplugin"))
        )
        user_script.refresh_from_db()
        self.assertEqual(user_script.environment_id, self.env.id)

    def test_queue_run_creates_run_via_seam(self):
        self.api.upsert(key="backup", code="x", environment=self.env)
        with mock.patch("core.tasks.queue_script_run") as q:
            run = self.api.queue_run("backup")
        q.assert_called_once()
        self.assertEqual(run.workspace_id, self.ws.id)
        self.assertEqual(run.status, Run.Status.PENDING)


class SecretAPITests(TestCase):
    def setUp(self):
        self.ws = Workspace.get_default()

    def test_owned_upsert_idempotent_clean_key(self):
        api = SecretAPI("myplugin")
        s1 = api.upsert("R2_BUCKET", "one")
        s2 = api.upsert("R2_BUCKET", "two")
        self.assertEqual(s1.pk, s2.pk)
        self.assertEqual(s2.owner_plugin, "myplugin")
        self.assertEqual(s2.owner_key, "R2_BUCKET")
        self.assertEqual(s2.get_decrypted_value(), "two")
        self.assertEqual(s2.get_clean_name(), "R2_BUCKET")

    def test_two_plugins_same_clean_key(self):
        a = SecretAPI("plugin_a").upsert("R2_BUCKET", "a")
        b = SecretAPI("plugin_b").upsert("R2_BUCKET", "b")
        self.assertNotEqual(a.pk, b.pk)

    def test_legacy_lane_owner_none(self):
        api = SecretAPI()  # owner=None
        s = api.upsert("PLAIN_KEY", "v")
        self.assertIsNone(s.owner_plugin)
        # idempotent by key in the legacy lane too
        s2 = api.upsert("PLAIN_KEY", "v2")
        self.assertEqual(s.pk, s2.pk)
        self.assertEqual(s2.get_decrypted_value(), "v2")

    def test_grant_idempotent(self):
        env = Environment.objects.create(name="e", path="grenv")
        script = ScriptAPI("myplugin").upsert(key="k", code="x", environment=env)
        secret = SecretAPI("myplugin").upsert("API_KEY", "v")
        g1 = SecretAPI("myplugin").grant(script, secret)
        g2 = SecretAPI("myplugin").grant(script, secret)
        self.assertEqual(g1.pk, g2.pk)
        self.assertEqual(SecretGrant.objects.filter(script=script).count(), 1)


class DataStoreAPITests(TestCase):
    def setUp(self):
        self.ws = Workspace.get_default()

    def test_auto_naming_and_entries(self):
        api = DataStoreAPI("myplugin")
        store = api.upsert("state", description="plugin state")
        self.assertEqual(store.name, "myplugin:state")
        self.assertEqual(store.model.owner_plugin, "myplugin")
        self.assertEqual(store.model.owner_key, "state")

        store.set("config", {"retries": 3})
        self.assertEqual(api.get("state").get("config"), {"retries": 3})

    def test_upsert_idempotent(self):
        api = DataStoreAPI("myplugin")
        a = api.upsert("state")
        b = api.upsert("state")
        self.assertEqual(a.model.pk, b.model.pk)
        self.assertEqual(DataStore.objects.filter(name="myplugin:state").count(), 1)

    def test_legacy_lane_raw_name(self):
        store = DataStoreAPI().upsert("plain_store")
        self.assertEqual(store.name, "plain_store")
        self.assertIsNone(store.model.owner_plugin)


class EnvironmentAPITests(TestCase):
    def test_list_and_get(self):
        Environment.objects.create(name="alpha", path="p1")
        Environment.objects.create(name="beta", path="p2")
        api = EnvironmentAPI()
        names = {e.name for e in api.list()}
        self.assertTrue({"alpha", "beta"}.issubset(names))
        self.assertEqual(api.get("alpha").name, "alpha")
        self.assertIsNone(api.get("nope"))


class ScheduleAPITests(TestCase):
    def setUp(self):
        self.ws = Workspace.get_default()
        self.env = Environment.objects.create(name="e", path="schedenv")

    def test_sync_creates_active_schedule(self):
        script = ScriptAPI("myplugin").upsert(key="backup", code="x", environment=self.env)
        with mock.patch("core.services.schedule_service.ScheduleService.sync_schedule"):
            sched = ScheduleAPI("myplugin").sync(
                script, mode=ScriptSchedule.RunMode.INTERVAL, interval_minutes=60
            )
        self.assertTrue(sched.is_active)
        self.assertEqual(sched.interval_minutes, 60)
