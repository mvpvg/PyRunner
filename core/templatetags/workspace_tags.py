"""
Template tags for workspace-aware URLs (tenancy Decision 1: URL-scoped prefix).

``{% ws_url 'cpanel:script_list' %}`` behaves like ``{% url %}`` but carries the
active workspace prefix (``/cpanel/w/<id>/…``) **only** when the switcher is
active — i.e. the user belongs to 2+ workspaces. A single-workspace instance
keeps emitting the canonical unprefixed URL, so it stays byte-for-byte identical.
"""

from django import template
from django.urls import NoReverseMatch, reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def ws_url(context, view_name, *args, **kwargs):
    """Reverse ``view_name``, prefixing with the active workspace when relevant.

    Falls back to the plain canonical reverse for anonymous users, single-
    workspace instances, non-``cpanel:`` names, or if the prefixed route doesn't
    exist — so it is always safe to use in place of ``{% url %}``.
    """
    request = context.get("request")
    workspace = getattr(request, "workspace", None) if request is not None else None
    show_switcher = context.get("show_workspace_switcher", False)

    if show_switcher and workspace is not None and view_name.startswith("cpanel:"):
        prefixed = "cpanel_ws:" + view_name.split(":", 1)[1]
        try:
            if kwargs:
                return reverse(
                    prefixed, kwargs={**kwargs, "workspace_id": workspace.id}
                )
            # workspace_id is the first captured group in the prefixed pattern,
            # so it leads the positional args.
            return reverse(prefixed, args=[workspace.id, *args])
        except NoReverseMatch:
            pass

    return reverse(view_name, args=args, kwargs=kwargs)
