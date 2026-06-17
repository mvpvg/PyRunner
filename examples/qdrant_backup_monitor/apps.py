from core.plugins import NavItem, PluginAppConfig, PyRunnerPlugin


class QdrantBackupMonitorConfig(PluginAppConfig):
    name = "plugins.qdrant_backup_monitor"
    label = "qdrant_backup_monitor"
    plugin = PyRunnerPlugin(
        slug="qdrant_backup_monitor",
        name="Qdrant Backup Monitor",
        version="1.0.0",
        nav_items=[
            NavItem(
                label="Qdrant Backups",
                url_name="qdrant_backup_monitor:index",
                # server-stack icon (fits "backups / snapshots")
                icon_svg=(
                    '<path stroke-linecap="round" stroke-linejoin="round" '
                    'd="M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 '
                    '01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 '
                    '2 0 00-2-2M5 12h14M8 7h.01M8 16h.01"/>'
                ),
            )
        ],
    )
