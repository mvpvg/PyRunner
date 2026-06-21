"""
Sandbox Stage 2a — host capability probe (detection half).

The probe tells an instance which protection tier it can deliver (full /
rlimits_only / none) and degrades honestly. Most of these tests mock the host
primitives (``os.name``, ``shutil.which``, ``subprocess.run``) so the same
matrix runs on the Windows dev box and on Linux CI — the real nsjail/bwrap
execution is exercised by the dashboard "Test" button on the real stack.
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import GlobalSettings, User
from core.services import sandbox
from core.services.sandbox import (
    CAP_FULL,
    CAP_NONE,
    CAP_RLIMITS_ONLY,
    find_sandbox_tool,
    probe_sandbox,
    run_and_store_probe,
)

# Path prefixes for patching the host primitives the probe reads.
P_OS = "core.services.sandbox.os"
P_WHICH = "core.services.sandbox.shutil.which"
P_RUN = "core.services.sandbox.subprocess.run"


def _completed(returncode=0, stdout="", stderr=""):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


class FindSandboxToolTests(TestCase):
    def test_prefers_nsjail_then_bwrap(self):
        with mock.patch(P_WHICH, side_effect=lambda t: "/usr/bin/" + t):
            self.assertEqual(find_sandbox_tool(), "nsjail")

    def test_falls_back_to_bwrap(self):
        with mock.patch(P_WHICH, side_effect=lambda t: "/usr/bin/bwrap" if t == "bwrap" else None):
            self.assertEqual(find_sandbox_tool(), "bwrap")

    def test_none_when_no_tool(self):
        with mock.patch(P_WHICH, return_value=None):
            self.assertIsNone(find_sandbox_tool())


class ProbeTierTests(TestCase):
    def test_non_posix_is_none(self):
        with mock.patch(f"{P_OS}.name", "nt"):
            result = probe_sandbox()
        self.assertEqual(result.capability, CAP_NONE)
        self.assertIsNone(result.tool)

    def test_posix_without_tool_is_rlimits_only(self):
        with mock.patch(f"{P_OS}.name", "posix"), mock.patch(P_WHICH, return_value=None):
            result = probe_sandbox()
        self.assertEqual(result.capability, CAP_RLIMITS_ONLY)
        self.assertIsNone(result.tool)
        self.assertIn("nsjail/bwrap", result.detail)

    def test_posix_tool_succeeds_is_full(self):
        with mock.patch(f"{P_OS}.name", "posix"), \
             mock.patch(P_WHICH, side_effect=lambda t: "/usr/bin/bwrap" if t == "bwrap" else None), \
             mock.patch(P_RUN, return_value=_completed(returncode=0)):
            result = probe_sandbox()
        self.assertEqual(result.capability, CAP_FULL)
        self.assertEqual(result.tool, "bwrap")

    def test_posix_tool_fails_degrades_to_rlimits_only(self):
        # userns blocked -> the tool exits non-zero; we degrade and surface why.
        with mock.patch(f"{P_OS}.name", "posix"), \
             mock.patch(P_WHICH, side_effect=lambda t: "/usr/bin/bwrap" if t == "bwrap" else None), \
             mock.patch(P_RUN, return_value=_completed(
                 returncode=1, stderr="bwrap: No permissions to creating new namespace")):
            result = probe_sandbox()
        self.assertEqual(result.capability, CAP_RLIMITS_ONLY)
        self.assertEqual(result.tool, "bwrap")
        self.assertIn("new namespace", result.detail)

    def test_posix_tool_timeout_degrades(self):
        import subprocess

        with mock.patch(f"{P_OS}.name", "posix"), \
             mock.patch(P_WHICH, side_effect=lambda t: "/usr/bin/nsjail" if t == "nsjail" else None), \
             mock.patch(P_RUN, side_effect=subprocess.TimeoutExpired("nsjail", 10)):
            result = probe_sandbox()
        self.assertEqual(result.capability, CAP_RLIMITS_ONLY)
        self.assertIn("timed out", result.detail)


class MinimalCommandTests(TestCase):
    def test_bwrap_command_unshares_user_and_binds_ro(self):
        cmd = sandbox._minimal_sandbox_command("bwrap", "/usr/bin/bwrap")
        self.assertEqual(cmd[0], "/usr/bin/bwrap")
        self.assertIn("--unshare-user", cmd)
        self.assertIn("--ro-bind", cmd)
        self.assertEqual(cmd[-1], "true")

    def test_nsjail_command_runs_once_in_chroot(self):
        cmd = sandbox._minimal_sandbox_command("nsjail", "/usr/bin/nsjail")
        self.assertEqual(cmd[0], "/usr/bin/nsjail")
        self.assertIn("-Mo", cmd)
        self.assertIn("--chroot", cmd)
        self.assertEqual(cmd[-1], "true")


class StoreProbeTests(TestCase):
    def test_run_and_store_caches_capability(self):
        fake = sandbox.SandboxProbeResult(CAP_FULL, tool="bwrap", detail="ok")
        with mock.patch.object(sandbox, "probe_sandbox", return_value=fake):
            result = run_and_store_probe()
        self.assertEqual(result.capability, CAP_FULL)
        gs = GlobalSettings.get_settings()
        self.assertEqual(gs.sandbox_capability, CAP_FULL)
        self.assertIsNotNone(gs.sandbox_checked_at)


class SandboxCheckCommandTests(TestCase):
    def test_reports_capability(self):
        fake = sandbox.SandboxProbeResult(CAP_RLIMITS_ONLY, tool=None, detail="no binary")
        out = StringIO()
        with mock.patch.object(sandbox, "probe_sandbox", return_value=fake):
            call_command("sandbox_check", stdout=out)
        self.assertIn("rlimits_only", out.getvalue())
        # Without --save the cache is untouched (still the default 'unknown').
        self.assertEqual(GlobalSettings.get_settings().sandbox_capability, "unknown")

    def test_save_caches(self):
        fake = sandbox.SandboxProbeResult(CAP_FULL, tool="nsjail", detail="ok")
        out = StringIO()
        with mock.patch.object(sandbox, "probe_sandbox", return_value=fake):
            call_command("sandbox_check", "--save", stdout=out)
        self.assertEqual(GlobalSettings.get_settings().sandbox_capability, CAP_FULL)
        self.assertIn("Saved", out.getvalue())


class SandboxTestViewTests(TestCase):
    def setUp(self):
        for target in (
            "core.services.setup_service.SetupService.is_setup_needed",
            "core.services.setup_service.SetupService.needs_admin_setup",
        ):
            p = mock.patch(target, return_value=False)
            p.start()
            self.addCleanup(p.stop)
        self.url = reverse("cpanel:sandbox_test")
        self.admin = User.objects.create(
            email="admin@example.com", is_staff=True, is_superuser=True
        )
        self.member = User.objects.create(email="m@example.com")

    def test_superuser_probe_stores_and_returns_json(self):
        fake = sandbox.SandboxProbeResult(CAP_FULL, tool="bwrap", detail="ok")
        self.client.force_login(self.admin)
        with mock.patch.object(sandbox, "probe_sandbox", return_value=fake):
            resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["capability"], CAP_FULL)
        self.assertEqual(data["tool"], "bwrap")
        self.assertEqual(GlobalSettings.get_settings().sandbox_capability, CAP_FULL)

    def test_non_superuser_forbidden(self):
        self.client.force_login(self.member)
        resp = self.client.post(self.url)
        # login_required passes (member is authed); the in-view superuser check
        # returns 403 without probing.
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(GlobalSettings.get_settings().sandbox_capability, "unknown")
