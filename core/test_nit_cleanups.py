"""
⚪ nit-batch cleanups — Group (a) regressions for the 2026-07 code review.

Correctness-flavored nits:
- 5.3  ``test_email_view`` had no in-body superuser check, unlike the sibling
       JSON settings endpoints — any logged-in member could fire test emails.
- 11.1 ``ai_provider_save_view`` took ``provider_id`` from a hidden POST field and
       fed it straight to ``.filter(pk=…)``; a non-UUID value 500'd instead of
       showing the friendly "Provider not found".
- 11.2 ``AISettingsForm`` silently stored ``active_ai_provider = None`` (AI off)
       for a valid-UUID-but-deleted provider while reporting success. It must
       raise a form error instead.
- 2.2  The first-user owner-upgrade swallowed all errors silently; a real failure
       now emits a ``logger.warning`` (user creation must still succeed).

Private-method / dead-path cleanups:
- 3.4a ``_backend_tool()`` now returns the resolved absolute path (was the bare
       tool name, then re-``shutil.which``ed at the call site).
- 3.4b / 4b.4  ``ScheduleService.calculate_next_run`` and
       ``ClaudeService.cli_available`` are the public names (callers no longer
       reach into an underscore-private method across modules).
"""

from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.forms import AISettingsForm
from core.models import (
    AIProvider,
    Environment,
    GlobalSettings,
    MagicToken,
    User,
    Workspace,
    WorkspaceMembership,
)


class _LoggedInMixin:
    """Setup that lets a logged-in user actually reach a cpanel view (setup
    complete, a superuser on record, and the caller has a membership)."""

    def _prepare_instance(self):
        gs = GlobalSettings.get_settings()
        gs.setup_completed = True
        gs.save()
        Environment.objects.create(
            name="env", path="p", is_active=True, is_default=True
        )
        self.ws = Workspace.get_default()
        # A superuser must exist or SetupMiddleware 302s everyone to /setup/admin/.
        User.objects.create(email="root@example.com", is_superuser=True, is_staff=True)

    def _login(self, *, superuser):
        user = User.objects.create(
            email=f"{'su' if superuser else 'mem'}@example.com",
            is_superuser=superuser,
            is_staff=superuser,
        )
        WorkspaceMembership.ensure(
            user,
            self.ws,
            role=WorkspaceMembership.ROLE_OWNER
            if superuser
            else WorkspaceMembership.ROLE_MEMBER,
        )
        self.client.force_login(user)
        return user


class TestEmailSuperuserGateTests(_LoggedInMixin, TestCase):
    """Review 5.3 — the test-email endpoint must gate on superuser in-body."""

    def setUp(self):
        self._prepare_instance()

    def test_plain_member_gets_403(self):
        self._login(superuser=False)
        resp = self.client.post(reverse("cpanel:test_email"))
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["success"])

    def test_superuser_passes_the_gate(self):
        self._login(superuser=True)
        resp = self.client.post(reverse("cpanel:test_email"))
        # Passes the permission gate; email backend is disabled by default, so the
        # body reports that (NOT a 403) — the point is the member above cannot.
        self.assertEqual(resp.status_code, 200)
        self.assertIn("disabled", resp.json()["error"].lower())


class AIProviderSaveBadIdTests(_LoggedInMixin, TestCase):
    """Review 11.1 — a malformed provider_id must not 500."""

    def setUp(self):
        self._prepare_instance()

    def test_non_uuid_provider_id_redirects_not_500(self):
        self._login(superuser=True)
        resp = self.client.post(
            reverse("cpanel:ai_provider_save"), {"provider_id": "not-a-uuid"}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("cpanel:services"))


class AISettingsStaleProviderTests(TestCase):
    """Review 11.2 — a valid-UUID-but-deleted provider is a form error, not a
    silent switch-off."""

    def setUp(self):
        self.settings = GlobalSettings.get_settings()

    def test_stale_provider_id_is_rejected(self):
        import uuid

        form = AISettingsForm(
            data={"claude_enabled": "on", "active_provider": str(uuid.uuid4())},
            instance=self.settings,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("active_provider", form.errors)
        self.assertIn("no longer exists", form.errors["active_provider"][0])

    def test_no_provider_is_allowed(self):
        form = AISettingsForm(
            data={"claude_enabled": "on", "active_provider": ""},
            instance=self.settings,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_existing_provider_is_accepted(self):
        provider = AIProvider.objects.create(
            provider_type=AIProvider.ProviderType.ANTHROPIC,
            name="Test provider",
            auth_method=AIProvider.AuthMethod.API_KEY,
        )
        form = AISettingsForm(
            data={"claude_enabled": "on", "active_provider": str(provider.id)},
            instance=self.settings,
        )
        self.assertTrue(form.is_valid(), form.errors)


class FirstUserBootstrapLoggingTests(TestCase):
    """Review 2.2 — a failed owner-upgrade logs a warning instead of vanishing,
    and never blocks the user's creation."""

    def test_owner_upgrade_failure_is_logged(self):
        User.objects.all().delete()  # force the first-user bootstrap path
        with mock.patch(
            "core.models.WorkspaceMembership.ensure",
            side_effect=Exception("db unavailable"),
        ):
            with self.assertLogs("core.models.user", level="WARNING") as cm:
                MagicToken.create_for_email("first@example.com")
        self.assertTrue(any("workspace owner" in line for line in cm.output))
        # The bootstrap admin is still created despite the membership failure.
        self.assertTrue(User.objects.filter(email="first@example.com").exists())


class BackendToolPathTests(SimpleTestCase):
    """Review 3.4a — ``_backend_tool`` returns the resolved path, not the name."""

    def test_returns_resolved_path(self):
        from core.executor_backends import sandboxed

        def fake_which(name):
            return f"/opt/sbin/{name}" if name == "bwrap" else None

        with mock.patch.object(sandboxed.shutil, "which", side_effect=fake_which):
            self.assertEqual(sandboxed._backend_tool(), "/opt/sbin/bwrap")

    def test_returns_none_when_absent(self):
        from core.executor_backends import sandboxed

        with mock.patch.object(sandboxed.shutil, "which", return_value=None):
            self.assertIsNone(sandboxed._backend_tool())


class PublicMethodRenameTests(SimpleTestCase):
    """Reviews 3.4b / 4b.4 — cross-module callers use public method names."""

    def test_schedule_calculate_next_run_is_public(self):
        from core.services.schedule_service import ScheduleService

        self.assertTrue(hasattr(ScheduleService, "calculate_next_run"))
        self.assertFalse(hasattr(ScheduleService, "_calculate_next_run"))

    def test_claude_cli_available_is_public(self):
        from core.services.claude_service import ClaudeService

        self.assertTrue(hasattr(ClaudeService, "cli_available"))
        self.assertFalse(hasattr(ClaudeService, "_cli_available"))
        self.assertIsInstance(ClaudeService.cli_available(), bool)


class SecretKeyMissingConfigTests(SimpleTestCase):
    """Review 1.3 — a missing SECRET_KEY raises a friendly ImproperlyConfigured
    (with the generate command), not a bare KeyError traceback."""

    def test_missing_secret_key_is_friendly(self):
        import os
        import subprocess
        import sys

        from django.conf import settings as dj_settings

        env = dict(os.environ)
        # "" reads as missing; python-dotenv's load_dotenv(override=False) won't refill
        # an already-present var, so a repo-local .env can't mask this in the subprocess.
        env["SECRET_KEY"] = ""
        proc = subprocess.run(
            [sys.executable, "-c", "import pyrunner.settings"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(dj_settings.BASE_DIR),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SECRET_KEY is required", proc.stderr)
        self.assertIn("get_random_secret_key", proc.stderr)
