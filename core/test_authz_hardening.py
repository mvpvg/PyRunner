"""
Security regression tests for the missing-authorization fixes in the
2026-07-05 audit (docs/SECURITY_AUDIT_2026-07-05.md).

A plain authenticated **member** (non-superuser) must not be able to reach
instance-global admin surfaces. The shared ``@superuser_required`` decorator
sends non-superusers to the login page (302 redirect), so a denied request is a
redirect to ``auth:login`` that performs no mutation.
"""
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from core.models import GlobalSettings, User


def _mock_setup(test):
    """Bypass the setup-wizard middleware so requests reach the target view."""
    for target in (
        "core.services.setup_service.SetupService.is_setup_needed",
        "core.services.setup_service.SetupService.needs_admin_setup",
    ):
        p = mock.patch(target, return_value=False)
        p.start()
        test.addCleanup(p.stop)


class SettingsAuthzTests(TestCase):
    """Vuln 3 — the six global-settings mutation handlers are superuser-only."""

    # Every settings handler that mutates the GlobalSettings singleton or performs
    # an instance-wide action. Members must be denied on all of them.
    GATED_ENDPOINTS = [
        "cpanel:toggle_global_pause",
        "cpanel:notification_settings",
        "cpanel:general_settings",
        "cpanel:retention_settings",
        "cpanel:worker_settings",
        "cpanel:manual_cleanup",
    ]

    def setUp(self):
        _mock_setup(self)
        self.member = User.objects.create(email="member@example.com")  # non-superuser
        self.superuser = User.objects.create(email="root@example.com", is_superuser=True)
        self.login_path = reverse("auth:login")

    def test_member_denied_on_every_gated_endpoint(self):
        self.client.force_login(self.member)
        for name in self.GATED_ENDPOINTS:
            with self.subTest(endpoint=name):
                resp = self.client.post(reverse(name), {})
                self.assertEqual(resp.status_code, 302)
                self.assertTrue(
                    resp["Location"].startswith(self.login_path),
                    f"{name} did not redirect a member to login: {resp['Location']}",
                )

    def test_member_cannot_hijack_email_backend(self):
        """Concrete escalation from the audit: a member repoints outbound mail to
        their own SMTP host to intercept a superuser password-reset. Must fail."""
        self.client.force_login(self.member)
        resp = self.client.post(
            reverse("cpanel:notification_settings"),
            {
                "email_backend": GlobalSettings.EmailBackend.SMTP,
                "smtp_host": "attacker.example",
                "smtp_port": "587",
                "default_notification_email": "attacker@evil.example",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith(self.login_path))
        gs = GlobalSettings.get_settings()
        self.assertEqual(gs.email_backend, GlobalSettings.EmailBackend.DISABLED)
        self.assertNotEqual(gs.smtp_host, "attacker.example")

    def test_superuser_still_reaches_settings_handlers(self):
        """The gate must not break admins: a superuser reaches the view and is
        redirected back to the settings page, not to login."""
        self.client.force_login(self.superuser)
        resp = self.client.post(reverse("cpanel:notification_settings"), {})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("cpanel:settings"))
