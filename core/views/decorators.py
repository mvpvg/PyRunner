"""Shared decorators for the HTML (cpanel) views.

``superuser_required`` gates instance-global / admin surfaces — settings,
services, backups, environments, plugins, the application log, user management,
and workspace creation. It REDIRECTS a non-superuser to the login page, which is
correct for full-page HTML views.

JSON / ``fetch()`` endpoints must NOT use this: a 302 to an HTML login page is
useless to a JSON client, so those endpoints do an in-body
``request.user.is_superuser`` check and return a JSON 403 instead (e.g. the
settings and services JSON endpoints).
"""
from django.contrib.auth.decorators import user_passes_test


def superuser_required(view_func):
    """Require an authenticated superuser; redirect others to ``auth:login``.

    (``LOGIN_URL`` is ``auth:login``, so this matches the app-wide login
    redirect.) For a JSON endpoint, use an in-body check instead — see the
    module docstring.
    """
    return user_passes_test(lambda u: u.is_superuser, login_url="auth:login")(view_func)
