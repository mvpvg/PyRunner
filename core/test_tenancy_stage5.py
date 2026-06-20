"""
Tenancy Stage 5 — backup/restore round-trips workspaces (leak matrix row 18).

Before Stage 5 a whole-instance restore serialized no workspace and re-created
every scoped row unscoped, collapsing all tenants into the default. Now the
backup carries a ``workspaces`` array + a ``workspace_id`` on each scoped row, so
a restore rebuilds the workspace topology and re-associates rows. A pre-1.2.0
backup (no workspace info) still restores — every row lands in the default
workspace, where it stays visible (no orphaning).
"""

from unittest import mock

from django.test import TestCase

from core.models import (
    DataStore,
    Environment,
    Run,
    Script,
    Secret,
    User,
    Workspace,
    WorkspaceMembership,
)
from core.services.backup_service import BackupService


class _Fixture(TestCase):
    def setUp(self):
        self.default = Workspace.get_default()
        self.ws_b = Workspace.objects.create(name="Tenant B")
        self.root = User.objects.create(email="root@example.com", is_superuser=True)

        # Shared env (lives in default); a default script and a ws_b script.
        self.env = Environment.objects.create(
            name="e", path="s5env", workspace=self.default
        )
        self.script_a = Script.objects.create(
            name="alpha", code="x", environment=self.env, workspace=self.default
        )
        self.script_b = Script.objects.create(
            name="beta", code="x", environment=self.env, workspace=self.ws_b
        )
        self.secret_a = Secret.objects.create(
            key="SECRET_A", encrypted_value="x", workspace=self.default
        )
        self.secret_b = Secret.objects.create(
            key="SECRET_B", encrypted_value="x", workspace=self.ws_b
        )
        self.store_a = DataStore.objects.create(name="alpha_store", workspace=self.default)
        self.store_b = DataStore.objects.create(name="beta_store", workspace=self.ws_b)
        self.run_b = Run.objects.create(script=self.script_b, workspace=self.ws_b)


class ExportTests(_Fixture):
    def test_export_includes_workspaces_and_ids(self):
        backup = BackupService.create_backup()
        self.assertIn("workspaces", backup)
        ws_ids = {w["id"] for w in backup["workspaces"]}
        self.assertIn(str(self.ws_b.id), ws_ids)
        self.assertIn(str(self.default.id), ws_ids)

        beta = next(s for s in backup["scripts"] if s["name"] == "beta")
        self.assertEqual(beta["workspace_id"], str(self.ws_b.id))
        alpha = next(s for s in backup["scripts"] if s["name"] == "alpha")
        self.assertEqual(alpha["workspace_id"], str(self.default.id))


class RoundTripTests(_Fixture):
    def test_recreates_workspace_and_preserves_associations(self):
        # Row 18: snapshot, simulate an instance that lost the non-default
        # workspace, restore, and confirm the tenant is rebuilt — not collapsed.
        ws_b_id = self.ws_b.id  # capture before delete() nulls the instance pk
        backup = BackupService.create_backup()
        self.ws_b.delete()
        self.assertFalse(Workspace.objects.filter(pk=ws_b_id).exists())

        result = BackupService.restore_backup(backup, current_user=self.root)
        self.assertTrue(result["success"], result.get("errors"))

        self.assertTrue(Workspace.objects.filter(pk=ws_b_id).exists())
        self.assertEqual(
            Script.objects.get(pk=self.script_b.id).workspace_id, ws_b_id
        )
        self.assertEqual(
            Secret.objects.get(pk=self.secret_b.id).workspace_id, ws_b_id
        )
        self.assertEqual(
            DataStore.objects.get(pk=self.store_b.id).workspace_id, ws_b_id
        )
        self.assertEqual(
            Run.objects.get(pk=self.run_b.id).workspace_id, ws_b_id
        )
        # Default-workspace rows stayed in the default — no merge either way.
        self.assertEqual(
            Script.objects.get(pk=self.script_a.id).workspace_id, self.default.id
        )

    def test_restorer_becomes_owner_of_recreated_workspace(self):
        ws_b_id = self.ws_b.id  # capture before delete() nulls the instance pk
        backup = BackupService.create_backup()
        self.ws_b.delete()
        BackupService.restore_backup(backup, current_user=self.root)
        m = WorkspaceMembership.objects.get(user=self.root, workspace_id=ws_b_id)
        self.assertEqual(m.role, WorkspaceMembership.ROLE_OWNER)

    def test_no_null_workspace_rows_after_restore(self):
        backup = BackupService.create_backup()
        BackupService.restore_backup(backup, current_user=self.root)
        self.assertFalse(Script.objects.filter(workspace__isnull=True).exists())
        self.assertFalse(Secret.objects.filter(workspace__isnull=True).exists())
        self.assertFalse(DataStore.objects.filter(workspace__isnull=True).exists())


class BackwardCompatTests(_Fixture):
    def test_pre_1_2_0_backup_maps_rows_to_default(self):
        # A pre-tenancy backup has no workspaces array and no workspace_id; every
        # scoped row must land in the default workspace (never NULL/invisible).
        backup = BackupService.create_backup()
        backup.pop("workspaces", None)
        for key in (
            "environments",
            "scripts",
            "secrets",
            "runs",
            "script_schedules",
            "datastores",
        ):
            for row in backup.get(key, []):
                row.pop("workspace_id", None)

        result = BackupService.restore_backup(backup, current_user=self.root)
        self.assertTrue(result["success"], result.get("errors"))

        self.assertEqual(
            Script.objects.get(pk=self.script_a.id).workspace_id, self.default.id
        )
        # The ws_b script collapses to default (expected: the old backup carried
        # no tenancy, so there is only one effective tenant to restore into).
        self.assertEqual(
            Script.objects.get(pk=self.script_b.id).workspace_id, self.default.id
        )
        self.assertFalse(Script.objects.filter(workspace__isnull=True).exists())


class SameNameAcrossWorkspacesTests(TestCase):
    """Rows 13/14 + 18 — same secret key / datastore name in two workspaces
    survive a backup round-trip (per-workspace uniqueness is restored intact)."""

    def setUp(self):
        self.default = Workspace.get_default()
        self.ws_b = Workspace.objects.create(name="B")
        self.root = User.objects.create(email="r@example.com", is_superuser=True)
        Secret.objects.create(key="API_KEY", encrypted_value="a", workspace=self.default)
        Secret.objects.create(key="API_KEY", encrypted_value="b", workspace=self.ws_b)
        DataStore.objects.create(name="results", workspace=self.default)
        DataStore.objects.create(name="results", workspace=self.ws_b)

    def test_round_trip(self):
        backup = BackupService.create_backup()
        result = BackupService.restore_backup(backup, current_user=self.root)
        self.assertTrue(result["success"], result.get("errors"))
        self.assertEqual(Secret.objects.filter(key="API_KEY").count(), 2)
        self.assertEqual(DataStore.objects.filter(name="results").count(), 2)
