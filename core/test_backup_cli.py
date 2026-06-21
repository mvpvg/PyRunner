"""
Step 4 — CLI restore (the import step migrate_db reuses).

A backup -> wipe -> restore round-trip through the `restore_backup` management
command, asserting data + datastore values come back and restored rows are
assigned to the default workspace. (migrate_db itself is verified end-to-end
against a real Postgres; this locks the reusable restore path into the suite.)
"""

import gzip
import json
import os
import tempfile

from django.core.management import call_command
from django.test import TestCase

from core.models import DataStore, DataStoreEntry, Environment, Script
from core.services.backup_service import BackupService


class RestoreBackupCommandTests(TestCase):
    def test_backup_wipe_restore_round_trip(self):
        env = Environment.objects.create(name="e", path="cliexp")
        Script.objects.create(name="cli_script", code="print(1)", environment=env)
        ds = DataStore.objects.create(name="cli_store")
        DataStoreEntry.objects.create(datastore=ds, key="k", value_json='{"a": 1}')

        backup = BackupService.create_backup(include_datastores=True)
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".json.gz", delete=False) as fh:
            fh.write(gzip.compress(json.dumps(backup).encode("utf-8")))
            path = fh.name

        try:
            # restore_backup deletes existing data then re-imports from the file.
            call_command("restore_backup", path, yes=True)
        finally:
            os.unlink(path)

        self.assertTrue(Script.objects.filter(name="cli_script").exists())
        self.assertEqual(DataStoreEntry.objects.get(key="k").get_value(), {"a": 1})
        # Restored rows are assigned to the default workspace.
        self.assertIsNotNone(Script.objects.get(name="cli_script").workspace_id)

    def test_refuses_without_yes(self):
        backup = BackupService.create_backup()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fh:
            json.dump(backup, fh)
            path = fh.name
        try:
            from django.core.management.base import CommandError

            with self.assertRaises(CommandError):
                call_command("restore_backup", path)  # no --yes
        finally:
            os.unlink(path)
