# Plugin Platform v2 (WS3) — resource ownership + scoped secrets.
#
# Adds the nullable owner_plugin/owner_key scoping axis to Script/Secret/DataStore,
# Script.injection_mode (default 'all' = today's behavior), the SecretGrant
# through-table, and re-scopes Secret uniqueness to (workspace, owner_plugin):
# user (owner-NULL) secrets keep their exact per-workspace rule, owned secrets get
# their own per-(workspace, owner_plugin) rule. Fully additive — every existing
# row is owner-NULL / injection_mode='all', byte-for-byte.

import django.db.models.deletion
import uuid
from django.db import migrations, models


def assert_no_secret_dups(apps, schema_editor):
    """Abort if existing data would violate the new Secret constraints.

    Pre-migration every row is ``owner_plugin IS NULL`` and the old
    ``uniq_secret_workspace_key`` already guaranteed per-workspace key uniqueness,
    so this is a belt-and-suspenders gate (it becomes meaningful if a future
    sideways data load introduced owned rows before this ran). Raises rather than
    letting ``AddConstraint`` fail with an opaque IntegrityError mid-migration.
    """
    Secret = apps.get_model("core", "Secret")
    seen = set()
    for ws_id, owner, key in Secret.objects.values_list(
        "workspace_id", "owner_plugin", "key"
    ):
        sig = (ws_id, owner, key)
        if sig in seen:
            raise RuntimeError(
                "Cannot apply 0038: duplicate secret "
                f"(workspace={ws_id}, owner_plugin={owner!r}, key={key!r}) "
                "violates the new (workspace, owner_plugin, key) uniqueness. "
                "Resolve the duplicate before migrating."
            )
        seen.add(sig)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_sandbox_policy_hierarchy"),
    ]

    operations = [
        migrations.CreateModel(
            name="SecretGrant",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "secret grant",
                "verbose_name_plural": "secret grants",
                "db_table": "secret_grants",
            },
        ),
        migrations.RemoveConstraint(
            model_name="secret",
            name="uniq_secret_workspace_key",
        ),
        migrations.RemoveConstraint(
            model_name="secret",
            name="uniq_secret_key_when_no_workspace",
        ),
        migrations.AddField(
            model_name="datastore",
            name="owner_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Stable per-owner handle for idempotent upsert (NULL = unmanaged).",
                max_length=100,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="datastore",
            name="owner_plugin",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Slug of the plugin that owns this data store (NULL = user/system).",
                max_length=100,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="script",
            name="injection_mode",
            field=models.CharField(
                choices=[
                    ("all", "All secrets (default)"),
                    ("selected", "Selected secrets only"),
                ],
                default="all",
                help_text="Which secrets to inject. 'all' = every workspace secret (today's behavior); 'selected' = only granted/same-owner/global secrets.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="script",
            name="owner_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Stable per-owner handle for idempotent upsert (NULL = unmanaged).",
                max_length=100,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="script",
            name="owner_plugin",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Slug of the plugin that owns this script (NULL = user-created).",
                max_length=100,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="secret",
            name="owner_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Stable per-owner handle for idempotent upsert (NULL = unmanaged).",
                max_length=100,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="secret",
            name="owner_plugin",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Slug of the plugin that owns this secret (NULL = user/system).",
                max_length=100,
                null=True,
            ),
        ),
        migrations.RunPython(assert_no_secret_dups, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="secret",
            constraint=models.UniqueConstraint(
                condition=models.Q(("owner_plugin__isnull", True)),
                fields=("workspace", "key"),
                name="uniq_secret_ws_key_user",
            ),
        ),
        migrations.AddConstraint(
            model_name="secret",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("workspace__isnull", True), ("owner_plugin__isnull", True)
                ),
                fields=("key",),
                name="uniq_secret_key_global_user",
            ),
        ),
        migrations.AddConstraint(
            model_name="secret",
            constraint=models.UniqueConstraint(
                condition=models.Q(("owner_plugin__isnull", False)),
                fields=("workspace", "owner_plugin", "key"),
                name="uniq_secret_ws_owner_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="secret",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("workspace__isnull", True), ("owner_plugin__isnull", False)
                ),
                fields=("owner_plugin", "key"),
                name="uniq_secret_owner_key_global",
            ),
        ),
        migrations.AddField(
            model_name="secretgrant",
            name="script",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="secret_grants",
                to="core.script",
            ),
        ),
        migrations.AddField(
            model_name="secretgrant",
            name="secret",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="grants",
                to="core.secret",
            ),
        ),
        migrations.AddField(
            model_name="script",
            name="granted_secrets",
            field=models.ManyToManyField(
                blank=True,
                help_text="Secrets explicitly attached to this script (selected mode).",
                related_name="granted_to_scripts",
                through="core.SecretGrant",
                through_fields=("script", "secret"),
                to="core.secret",
            ),
        ),
        migrations.AddConstraint(
            model_name="secretgrant",
            constraint=models.UniqueConstraint(
                fields=("script", "secret"), name="uniq_secret_grant"
            ),
        ),
    ]
