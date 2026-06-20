"""
Workspace management views (tenancy Stage 0).

Lets an operator create/rename workspaces and view members, so a single-workspace
instance can become multi-workspace and the switcher can appear. Superuser-gated
in Stage 0 — role-based (owner/admin) gating arrives with RBAC in a later stage.
Creating a workspace makes the creator its owner.

These are plumbing only: no scoped query filters by workspace yet.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import Workspace, WorkspaceMembership

MAX_NAME_LEN = 100


def is_admin(user) -> bool:
    """Stage 0 gate: workspace management is superuser-only for now."""
    return user.is_superuser


@login_required
@user_passes_test(is_admin)
def workspace_list_view(request: HttpRequest) -> HttpResponse:
    """List all workspaces with member counts (operator view)."""
    workspaces = Workspace.objects.annotate(
        member_count=Count("memberships", distinct=True)
    ).order_by("-is_default", "name")
    return render(
        request, "cpanel/workspaces/list.html", {"workspaces": workspaces}
    )


@login_required
@user_passes_test(is_admin)
@require_POST
def workspace_create_view(request: HttpRequest) -> HttpResponse:
    """Create a workspace; the creator becomes its owner."""
    name = (request.POST.get("name") or "").strip()[:MAX_NAME_LEN]
    if not name:
        messages.error(request, "Workspace name is required.")
        return redirect("cpanel:workspace_list")

    workspace = Workspace.objects.create(name=name)
    WorkspaceMembership.ensure(
        request.user, workspace, role=WorkspaceMembership.ROLE_OWNER
    )
    messages.success(request, f"Workspace '{name}' created.")
    return redirect("cpanel:workspace_list")


@login_required
@user_passes_test(is_admin)
@require_POST
def workspace_rename_view(request: HttpRequest, pk) -> HttpResponse:
    """Rename a workspace."""
    workspace = get_object_or_404(Workspace, pk=pk)
    name = (request.POST.get("name") or "").strip()[:MAX_NAME_LEN]
    if not name:
        messages.error(request, "Workspace name is required.")
        return redirect("cpanel:workspace_list")

    workspace.name = name
    workspace.save(update_fields=["name", "updated_at"])
    messages.success(request, "Workspace renamed.")
    return redirect("cpanel:workspace_list")


@login_required
@user_passes_test(is_admin)
def workspace_members_view(request: HttpRequest, pk) -> HttpResponse:
    """List the members of a workspace and their roles."""
    workspace = get_object_or_404(Workspace, pk=pk)
    memberships = workspace.memberships.select_related("user").order_by(
        "role", "user__email"
    )
    return render(
        request,
        "cpanel/workspaces/members.html",
        {"workspace": workspace, "memberships": memberships},
    )
