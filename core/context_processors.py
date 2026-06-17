"""
Context processors for PyRunner templates.
"""

from pyrunner.version import __version__


def pyrunner_version(request):
    """Add PyRunner version and update-availability info to template context."""
    context = {"pyrunner_version": __version__}

    # Whether a newer release is available (computed by the daily update check).
    # Wrapped defensively so a DB hiccup never breaks page rendering.
    try:
        from core.services.update_service import UpdateService

        context.update(UpdateService.get_update_context())
    except Exception:
        context.setdefault("update_available", False)
        context.setdefault("update_latest_version", "")

    return context


def plugin_nav(request):
    """Sidebar nav items contributed by active plugins (empty for anonymous).

    Wrapped defensively so a misbehaving plugin's nav can never break rendering.
    """
    try:
        from core.plugins import nav_for

        return {"plugin_nav": nav_for(getattr(request, "user", None))}
    except Exception:
        return {"plugin_nav": []}
