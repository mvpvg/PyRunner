from core.plugins import NavItem, PluginAppConfig, PyRunnerPlugin


class SalesDashboardConfig(PluginAppConfig):
    name = "plugins.sales_dashboard"
    label = "sales_dashboard"
    plugin = PyRunnerPlugin(
        slug="sales_dashboard",
        name="Sales Dashboard",
        version="1.0.0",
        nav_items=[
            NavItem(
                label="Sales Dashboard",
                url_name="sales_dashboard:index",
                icon_svg='<path stroke-linecap="round" stroke-linejoin="round" d="M3 3v18h18M7 14l3-3 3 3 5-5"/>',
            )
        ],
    )
