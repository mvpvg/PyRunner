from django.test import TestCase, modify_settings
from django.urls import reverse, NoReverseMatch

from core.models import MagicToken, User, UserInvite
from core.models.settings import GlobalSettings


@modify_settings(MIDDLEWARE={"remove": "core.middleware.SetupWizardMiddleware"})
class InviteOnlyRegistrationTests(TestCase):
    """Registration is invite-only, and the only ways to get an account are
    (1) the setup-wizard bootstrap (creates the admin *with* a password) or
    (2) accepting a valid invite via the set-password flow.

    The old passwordless magic-link login was removed (security audit Vuln 2),
    so the login view never provisions accounts and there is no on-screen or
    emailed login token. These tests pin that behaviour.

    (The setup-wizard middleware is removed for these tests so the login POST
    reaches ``login_view`` directly; it is orthogonal to the auth gate.)
    """

    def _disable_email(self):
        gs = GlobalSettings.get_settings()
        gs.email_backend = GlobalSettings.EmailBackend.DISABLED
        gs.save(update_fields=["email_backend"])

    # --- The removed magic-link surface is gone -----------------------------

    def test_magic_link_routes_removed(self):
        """The magic-link routes no longer resolve."""
        with self.assertRaises(NoReverseMatch):
            reverse("auth:magic_link_sent")
        with self.assertRaises(NoReverseMatch):
            reverse("auth:verify", kwargs={"token": "anything"})

    def test_login_never_provisions_an_account(self):
        """The former exploit: an anonymous POST cannot mint a login token or
        create/resurrect an account for any email — the login view only ever
        authenticates existing credentials."""
        self._disable_email()
        User.objects.create(email="admin@example.com")  # existing superuser target

        # Replay the old magic-link request shape against the login endpoint.
        resp = self.client.post(
            reverse("auth:login"),
            {"action": "magic_link", "magic_email": "admin@example.com"},
        )

        # Not logged in, and no token was minted for the target account.
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(MagicToken.objects.filter(email="admin@example.com").exists())
        # And a brand-new email is never auto-created by the login view.
        self.assertFalse(User.objects.filter(email="stranger@example.com").exists())

    # --- Invite onboarding = set-password flow ------------------------------

    def _create_invite(self, email):
        admin = User.objects.create(email="admin@example.com")
        return UserInvite.create_invite(email, created_by=admin)

    def test_accept_invite_shows_set_password_form(self):
        invite = self._create_invite("guest@example.com")
        resp = self.client.get(
            reverse("auth:accept_invite", kwargs={"token": invite.token})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Set your password")

    def test_accept_invite_creates_account_with_password(self):
        """A valid invite + password creates a usable-password account, marks the
        invite used, and logs the user in. No passwordless account is left."""
        invite = self._create_invite("guest@example.com")

        resp = self.client.post(
            reverse("auth:accept_invite", kwargs={"token": invite.token}),
            {"password": "s3cretpassword", "password_confirm": "s3cretpassword"},
        )

        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(email="guest@example.com")
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password("s3cretpassword"))
        self.assertFalse(user.is_superuser)  # invitees are plain members
        self.assertTrue(user.is_verified)
        invite.refresh_from_db()
        self.assertIsNotNone(invite.used_at)
        # Logged in as the new user.
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_accept_invalid_invite_creates_no_account(self):
        resp = self.client.post(
            reverse("auth:accept_invite", kwargs={"token": "bogus-token"}),
            {"password": "s3cretpassword", "password_confirm": "s3cretpassword"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid invite link")
        self.assertFalse(User.objects.filter(email="guest@example.com").exists())

    def test_accept_expired_invite_creates_no_account(self):
        from datetime import timedelta
        from django.utils import timezone

        invite = self._create_invite("guest@example.com")
        invite.expires_at = timezone.now() - timedelta(days=1)
        invite.save(update_fields=["expires_at"])

        resp = self.client.post(
            reverse("auth:accept_invite", kwargs={"token": invite.token}),
            {"password": "s3cretpassword", "password_confirm": "s3cretpassword"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(email="guest@example.com").exists())
