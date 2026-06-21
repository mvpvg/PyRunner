"""
Plugin Platform v2 — Stage 2b-3a (secret-attach backend) tests.

The endpoints + grant reconciliation behind the script-form secret-attach UI:
  * scan-env-refs — server-side scan of a script body for os.environ keys it uses,
    flagging which already exist as secrets in the workspace.
  * secret-picker — autocomplete over workspace secrets, owner-tagged.
  * grant reconciliation — make a script's SecretGrant set match the submitted ids.
"""

from unittest import mock

from django.test import TestCase
from django.urls import reverse

from core.models import (
    Environment, Script, Secret, SecretGrant, User, Workspace, WorkspaceMembership,
)
from core.views.scripts import _reconcile_grants


def _secret(key, value, ws, owner_plugin=None):
    s = Secret(key=key, workspace=ws, owner_plugin=owner_plugin)
    s.set_value(value)
    s.save()
    return s


class _LoggedIn(TestCase):
    def setUp(self):
        for target in (
            "core.services.setup_service.SetupService.is_setup_needed",
            "core.services.setup_service.SetupService.needs_admin_setup",
        ):
            p = mock.patch(target, return_value=False); p.start(); self.addCleanup(p.stop)
        self.ws = Workspace.get_default()
        self.user = User.objects.create(email="u@example.com")
        WorkspaceMembership.ensure(self.user, self.ws, WorkspaceMembership.ROLE_MEMBER)
        self.client.force_login(self.user)
        self.env = Environment.objects.create(name="e", path="saenv")


class ScanEnvRefsTests(_LoggedIn):
    def test_scan_finds_refs_and_flags_existing_secrets(self):
        _secret("API_KEY", "v", self.ws)
        code = (
            "import os\n"
            "a = os.environ['API_KEY']\n"
            "b = os.environ.get('MISSING_ONE')\n"
            "c = os.getenv(\"DB_URL\")\n"
        )
        resp = self.client.post(reverse("cpanel:scan_env_refs"), {"code": code})
        self.assertEqual(resp.status_code, 200)
        refs = {r["key"]: r["secret_id"] for r in resp.json()["refs"]}
        self.assertEqual(set(refs), {"API_KEY", "MISSING_ONE", "DB_URL"})
        self.assertIsNotNone(refs["API_KEY"])      # exists → attachable
        self.assertIsNone(refs["MISSING_ONE"])     # unknown → offer inline create

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse("cpanel:scan_env_refs")).status_code, 405)


class SecretPickerTests(_LoggedIn):
    def test_picker_filters_and_tags_owner(self):
        _secret("ALPHA_KEY", "v", self.ws)
        _secret("BETA_KEY", "v", self.ws, owner_plugin="myplugin")
        resp = self.client.get(reverse("cpanel:secret_picker"), {"q": "key"})
        self.assertEqual(resp.status_code, 200)
        by_key = {s["key"]: s for s in resp.json()["secrets"]}
        self.assertEqual(set(by_key), {"ALPHA_KEY", "BETA_KEY"})
        self.assertEqual(by_key["ALPHA_KEY"]["owner_plugin"], "")          # System
        self.assertEqual(by_key["BETA_KEY"]["owner_plugin"], "myplugin")   # owner-tagged

    def test_picker_query_narrows(self):
        _secret("ALPHA_KEY", "v", self.ws)
        _secret("ZED", "v", self.ws)
        keys = {s["key"] for s in self.client.get(reverse("cpanel:secret_picker"), {"q": "alpha"}).json()["secrets"]}
        self.assertEqual(keys, {"ALPHA_KEY"})


class ReconcileGrantsTests(_LoggedIn):
    def test_reconcile_adds_and_removes(self):
        script = Script.objects.create(
            name="s", code="x", environment=self.env, workspace=self.ws,
            injection_mode=Script.InjectionMode.SELECTED,
        )
        s1 = _secret("ONE", "v", self.ws)
        s2 = _secret("TWO", "v", self.ws)
        s3 = _secret("THREE", "v", self.ws)

        _reconcile_grants(script, [str(s1.id), str(s2.id)], self.ws)
        self.assertEqual(
            {g.secret_id for g in SecretGrant.objects.filter(script=script)}, {s1.id, s2.id}
        )
        # Drop s1, add s3.
        _reconcile_grants(script, [str(s2.id), str(s3.id)], self.ws)
        self.assertEqual(
            {g.secret_id for g in SecretGrant.objects.filter(script=script)}, {s2.id, s3.id}
        )

    def test_reconcile_ignores_secrets_outside_workspace(self):
        script = Script.objects.create(
            name="s", code="x", environment=self.env, workspace=self.ws,
            injection_mode=Script.InjectionMode.SELECTED,
        )
        other_ws = Workspace.objects.create(name="Other")
        foreign = _secret("FOREIGN", "v", other_ws)
        _reconcile_grants(script, [str(foreign.id)], self.ws)
        self.assertEqual(SecretGrant.objects.filter(script=script).count(), 0)

    def test_create_via_form_attaches_grants(self):
        s1 = _secret("ATTACH_ME", "v", self.ws)
        resp = self.client.post(reverse("cpanel:script_create"), {
            "name": "Selected script", "code": "print(1)", "environment": str(self.env.id),
            "timeout_seconds": "60", "injection_mode": "selected",
            "isolation_mode": "inherit", "notify_on": "never",
            "granted_secret_ids": [str(s1.id)],
        })
        self.assertEqual(resp.status_code, 302, getattr(resp, "content", b"")[:500])
        script = Script.objects.get(name="Selected script")
        self.assertEqual(script.injection_mode, "selected")
        self.assertTrue(SecretGrant.objects.filter(script=script, secret=s1, active=True).exists())


class RenderTests(_LoggedIn):
    def test_create_page_renders_secret_attach(self):
        resp = self.client.get(reverse("cpanel:script_create"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('name="injection_mode"', body)
        self.assertIn("Auto-detect from code", body)
        self.assertIn(reverse("cpanel:secret_picker"), body)

    def test_edit_page_prefills_granted_chips(self):
        script = Script.objects.create(
            name="s", code="x", environment=self.env, workspace=self.ws,
            injection_mode=Script.InjectionMode.SELECTED,
        )
        s1 = _secret("ATTACHED", "v", self.ws)
        SecretGrant.objects.create(script=script, secret=s1, active=True)
        resp = self.client.get(reverse("cpanel:script_edit", args=[script.pk]))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("ATTACHED", body)
        self.assertIn(f'value="{s1.id}"', body)  # hidden granted_secret_ids prefilled
