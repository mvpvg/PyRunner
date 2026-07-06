from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    """Create the DatabaseCache table (see CACHES in settings.py).

    The table name is passed explicitly so the table is created regardless of
    whether REDIS_URL is set at migrate time — toggling Redis on or off later
    never leaves the default DatabaseCache backend without its table.
    createcachetable is a no-op if the table already exists.
    """
    call_command(
        "createcachetable",
        "pyrunner_cache",
        database=schema_editor.connection.alias,
        verbosity=0,
    )


def drop_cache_table(apps, schema_editor):
    schema_editor.execute("DROP TABLE IF EXISTS pyrunner_cache")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0041_pyai_settings"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
