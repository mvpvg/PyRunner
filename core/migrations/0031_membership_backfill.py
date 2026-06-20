"""
Membership backfill (tenancy, Stage 0).

Seeds exactly one WorkspaceMembership per existing user in the default workspace
so every user has somewhere to act and the active-workspace middleware can always
resolve a workspace. Existing superusers become ``owner``; everyone else becomes
``member``. A single-workspace instance is therefore unchanged: each user is a
member of the one workspace, the switcher stays hidden, behavior is byte-for-byte.

Written to be idempotent (safe to re-run; never duplicates a membership) and
reversible. Uses historical models via ``apps.get_model`` — never the live model
classes.
"""

from django.db import migrations


def backfill_memberships(apps, schema_editor):
    User = apps.get_model("core", "User")
    Workspace = apps.get_model("core", "Workspace")
    WorkspaceMembership = apps.get_model("core", "WorkspaceMembership")

    # The default workspace is created by 0029; reuse it (or, defensively, the
    # earliest default) so this never invents a second one.
    default = Workspace.objects.filter(is_default=True).order_by("created_at").first()
    if default is None:
        # Nothing to attach to (a DB with no default workspace yet) — no-op.
        return

    for user in User.objects.all().iterator():
        role = "owner" if user.is_superuser else "member"
        membership, created = WorkspaceMembership.objects.get_or_create(
            user=user, workspace=default, defaults={"role": role}
        )
        # Idempotent upgrade only: an existing membership is promoted to owner for
        # a superuser, never silently downgraded.
        if not created and role == "owner" and membership.role != "owner":
            membership.role = "owner"
            membership.save(update_fields=["role", "updated_at"])


def unbackfill(apps, schema_editor):
    # Reverse cleanly: drop every membership in the default workspace.
    Workspace = apps.get_model("core", "Workspace")
    WorkspaceMembership = apps.get_model("core", "WorkspaceMembership")

    default = Workspace.objects.filter(is_default=True).order_by("created_at").first()
    if default is None:
        return
    WorkspaceMembership.objects.filter(workspace=default).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0030_workspace_membership"),
    ]

    operations = [
        migrations.RunPython(backfill_memberships, unbackfill),
    ]
