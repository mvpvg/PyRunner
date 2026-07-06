"""
Context processors for PyRunner templates.
"""

from pyrunner.version import __version__, IS_BETA


def pyrunner_version(request):
    """Add PyRunner version and update-availability info to template context."""
    context = {"pyrunner_version": __version__, "pyrunner_is_beta": IS_BETA}

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


def workspaces(request):
    """Active workspace + the user's workspaces, for the switcher (tenancy Stage 0).

    Defensive: anonymous users, an unresolved workspace, or a DB hiccup all yield
    safe empty defaults so rendering never breaks. The switcher is shown only when
    the user belongs to 2+ workspaces — a single-workspace instance hides it and
    stays byte-for-byte identical to before tenancy.
    """
    ctx = {
        "active_workspace": getattr(request, "workspace", None),
        "user_workspaces": [],
        "show_workspace_switcher": False,
    }

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return ctx

    try:
        from core.models import Workspace

        user_workspaces = list(
            Workspace.for_user(user).order_by("-is_default", "name")
        )
        ctx["user_workspaces"] = user_workspaces
        ctx["show_workspace_switcher"] = len(user_workspaces) >= 2
    except Exception:
        pass

    return ctx
