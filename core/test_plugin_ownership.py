"""
Plugin Platform v2 — Stage 2a (Ownership + Scoped Secrets, WS3) tests.

Covers the data-model + executor half of WS3:
  * Secret uniqueness re-scoped to (workspace, owner_plugin) — user secrets keep
    their exact per-workspace rule; owned secrets get their own per-owner rule.
  * The executor's injection-mode resolution: 'all' (default) = user secrets only
    (byte-identical to pre-v2); 'selected' = global + same-owner + granted, by
    clean name, with global < same-owner < grant precedence.
  * DataStore name stays per-workspace unique regardless of owner.
  * PYRUNNER_OWNER_PLUGIN exposed to an owned script's run env.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase

from core.executor import _build_script_environment, resolve_secrets_for_run
from core.models import (
    DataStore,
    Environment,
    Run,
    Script,
    Secret,
    SecretGrant,
    Workspace,
)


def _secret(key, value, *, workspace=None, owner_plugin=None, owner_key=None):
    s = Secret(
        key=key, workspace=workspace, owner_plugin=owner_plugin, owner_key=owner_key
    )
    s.set_value(value)
    s.save()
    return s


class SecretUniquenessTests(TestCase):
    def setUp(self):
        self.ws = Workspace.get_default()
        self.ws2 = Workspace.objects.create(name="Other")

    def test_two_user_secrets_same_key_same_workspace_rejected(self):
        _secret("API_KEY", "a", workspace=self.ws)
        with self.assertRaises(IntegrityError), transaction.atomic():
            _secret("API_KEY", "b", workspace=self.ws)

    def test_user_secrets_same_key_different_workspace_ok(self):
        _secret("API_KEY", "a", workspace=self.ws)
        _secret("API_KEY", "b", workspace=self.ws2)  # different workspace → ok

    def test_two_user_secrets_same_key_null_workspace_rejected(self):
        _secret("GLOBAL", "a", workspace=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            _secret("GLOBAL", "b", workspace=None)

    def test_two_plugins_same_key_same_workspace_ok(self):
        _secret("R2_BUCKET", "a", workspace=self.ws, owner_plugin="plugin_a")
        # A different owner may reuse the clean key in the same workspace.
        _secret("R2_BUCKET", "b", workspace=self.ws, owner_plugin="plugin_b")

    def test_same_plugin_same_key_same_workspace_rejected(self):
        _secret("R2_BUCKET", "a", workspace=self.ws, owner_plugin="plugin_a")
        with self.assertRaises(IntegrityError), transaction.atomic():
            _secret("R2_BUCKET", "b", workspace=self.ws, owner_plugin="plugin_a")

    def test_user_and_owned_same_key_same_workspace_ok(self):
        _secret("API_KEY", "user", workspace=self.ws)
        # An owned secret never collides with the user secret of the same key.
        _secret("API_KEY", "owned", workspace=self.ws, owner_plugin="plugin_a")


class InjectionModeTests(TestCase):
    def setUp(self):
        self.ws = Workspace.get_default()
        self.env = Environment.objects.create(name="e", path="ownenv")

        # Secrets: a global user secret, two owned secrets (different owners), and
        # a SHARED key existing both globally and under plugin_a (clash test).
        _secret("GLOBAL_KEY", "g", workspace=self.ws)
        _secret("OWNED_KEY", "owned-a", workspace=self.ws, owner_plugin="plugin_a")
        _secret("OTHER_KEY", "owned-b", workspace=self.ws, owner_plugin="plugin_b")
        _secret("SHARED", "global-val", workspace=self.ws)
        _secret("SHARED", "plugin-a-val", workspace=self.ws, owner_plugin="plugin_a")

        self.user_script = Script.objects.create(
            name="user", code="x", environment=self.env, workspace=self.ws
        )
        self.owned_script = Script.objects.create(
            name="owned", code="x", environment=self.env, workspace=self.ws,
            owner_plugin="plugin_a", owner_key="backup",
            injection_mode=Script.InjectionMode.SELECTED,
        )
        self.user_run = Run.objects.create(script=self.user_script, workspace=self.ws)
        self.owned_run = Run.objects.create(script=self.owned_script, workspace=self.ws)

    def test_all_mode_injects_only_user_secrets(self):
        out = resolve_secrets_for_run(self.user_run)
        self.assertEqual(out.get("GLOBAL_KEY"), "g")
        self.assertEqual(out.get("SHARED"), "global-val")  # global wins for user script
        self.assertNotIn("OWNED_KEY", out)   # plugin secret never leaks into a user script
        self.assertNotIn("OTHER_KEY", out)

    def test_selected_includes_global_and_same_owner_only(self):
        out = resolve_secrets_for_run(self.owned_run)
        self.assertEqual(out.get("GLOBAL_KEY"), "g")     # explicitly-global injected
        self.assertEqual(out.get("OWNED_KEY"), "owned-a")  # same-owner injected
        self.assertNotIn("OTHER_KEY", out)               # other plugin's secret excluded

    def test_selected_same_owner_overrides_global_on_clean_name_clash(self):
        out = resolve_secrets_for_run(self.owned_run)
        self.assertEqual(out.get("SHARED"), "plugin-a-val")  # same-owner wins

    def test_selected_injects_active_grant_and_skips_inactive(self):
        # Grant plugin_b's secret to plugin_a's script (it would not inject otherwise).
        other = Secret.objects.get(key="OTHER_KEY", owner_plugin="plugin_b")
        grant = SecretGrant.objects.create(script=self.owned_script, secret=other)

        out = resolve_secrets_for_run(self.owned_run)
        self.assertEqual(out.get("OTHER_KEY"), "owned-b")  # granted → injected

        grant.active = False
        grant.save(update_fields=["active"])
        out = resolve_secrets_for_run(self.owned_run)
        self.assertNotIn("OTHER_KEY", out)  # inactive grant → not injected

    def test_masking_set_matches_injection_set(self):
        # The env builder injects exactly the resolved clean names (shared resolver
        # → masking can't drift). No other workspace's owned value bleeds in.
        env = _build_script_environment(run=self.owned_run)
        resolved = resolve_secrets_for_run(self.owned_run)
        for name, value in resolved.items():
            self.assertEqual(env.get(name), value)
        self.assertNotIn("OTHER_KEY", env)

    def test_owner_plugin_env_set_for_owned_script_only(self):
        self.assertEqual(
            _build_script_environment(run=self.owned_run).get("PYRUNNER_OWNER_PLUGIN"),
            "plugin_a",
        )
        self.assertNotIn(
            "PYRUNNER_OWNER_PLUGIN", _build_script_environment(run=self.user_run)
        )


class DataStoreOwnershipTests(TestCase):
    def setUp(self):
        self.ws = Workspace.get_default()

    def test_name_unique_per_workspace_regardless_of_owner(self):
        DataStore.objects.create(name="state", workspace=self.ws, owner_plugin="plugin_a")
        # owner is grouping metadata only — it is NOT part of the uniqueness key,
        # so a second 'state' in the same workspace is rejected even under a
        # different owner (the SDK avoids this by auto-naming "<slug>:<key>").
        with self.assertRaises(IntegrityError), transaction.atomic():
            DataStore.objects.create(
                name="state", workspace=self.ws, owner_plugin="plugin_b"
            )

    def test_owner_fields_default_null(self):
        ds = DataStore.objects.create(name="plain", workspace=self.ws)
        self.assertIsNone(ds.owner_plugin)
        self.assertIsNone(ds.owner_key)
