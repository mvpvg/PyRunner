from django.test import TestCase, modify_settings
from django.urls import reverse

from core.models import MagicToken, User, UserInvite
from core.models.settings import GlobalSettings


@modify_settings(MIDDLEWARE={"remove": "core.middleware.SetupWizardMiddleware"})
class InviteOnlyRegistrationTests(TestCase):
    """Registration is invite-only, enforced in code in the magic-link flow.

    The legacy ``allow_registration`` flag is intentionally dormant: even when it
    is flipped ON, an un-invited stranger must not be able to self-register. The
    only ways to get an account are (1) being the very first user (bootstrap) or
    (2) holding a valid invite.

    (The setup-wizard middleware is removed for these tests so the login POST
    reaches ``login_view`` directly; it is orthogonal to the auth gate.)
    """

    def _disable_email(self):
        # Avoid any SMTP attempt on the success path; the magic link is just
        # shown on-screen when the email backend is disabled.
        gs = GlobalSettings.get_settings()
        gs.email_backend = GlobalSettings.EmailBackend.DISABLED
        gs.save(update_fields=["email_backend"])

    def _request_magic_link(self, email):
        return self.client.post(
            reverse("auth:login"),
            {"action": "magic_link", "magic_email": email},
        )

    def test_first_user_can_bootstrap(self):
        """Empty DB: the first user is allowed in without an invite."""
        self._disable_email()
        self.assertEqual(User.objects.count(), 0)

        self._request_magic_link("founder@example.com")

        self.assertTrue(User.objects.filter(email="founder@example.com").exists())

    def test_uninvited_blocked_even_with_flag_on(self):
        """Key proof: the dormant flag cannot re-open registration.

        With an admin already present and ``allow_registration`` deliberately
        set True, an un-invited email still cannot self-register.
        """
        self._disable_email()
        User.objects.create(email="admin@example.com")  # not the first user anymore
        gs = GlobalSettings.get_settings()
        gs.allow_registration = True  # flip the dormant flag ON on purpose
        gs.save(update_fields=["allow_registration"])

        self._request_magic_link("stranger@example.com")

        # No account and no magic token were created for the stranger.
        self.assertFalse(User.objects.filter(email="stranger@example.com").exists())
        self.assertFalse(MagicToken.objects.filter(email="stranger@example.com").exists())

    def test_invited_user_can_register(self):
        """A valid invite lets a brand-new email through."""
        self._disable_email()
        admin = User.objects.create(email="admin@example.com")
        UserInvite.create_invite("guest@example.com", created_by=admin)

        self._request_magic_link("guest@example.com")

        self.assertTrue(User.objects.filter(email="guest@example.com").exists())
