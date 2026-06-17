# Writing a PyRunner plugin

A plugin is a self-contained Django app that adds UI and behavior to PyRunner
**without editing any core file**. Admins upload it, activate it, and it serves at
`/plugins/<slug>/` and appears in the console sidebar.

The cardinal rule of the system: **a broken plugin can never break the main
site.** "Installed" (files on disk) is not "active" (loaded), and nothing risky
loads into the live process without passing an isolated preflight first.

---

## Folder layout

```
<slug>/                      # e.g. my_flows — a self-contained Django app
  __init__.py
  apps.py                    # subclass core.plugins.PluginAppConfig
  plugin.json                # manifest: slug (must match folder), name, version
  urls.py                    # app_name = "<slug>"; auto-mounted at /plugins/<slug>/
  views.py
  models.py                  # optional; own tables (namespaced by app label)
  migrations/                # optional; required if you have models
  templates/<slug>/...       # extend "base.html" so pages match the console
  static/<slug>/...          # optional
```

The `slug` must match `^[a-z][a-z0-9_]*$` (a valid Python identifier) and must be
the same in three places: the folder name, `plugin.json`'s `slug`, and the
`PyRunnerPlugin(slug=...)` in `apps.py`.

## `plugin.json`

```json
{
    "slug": "my_flows",
    "name": "My Flows",
    "version": "1.0.0",
    "min_pyrunner": "1.10.0",
    "description": "What this plugin does."
}
```

Only `slug` is strictly required; `name`/`version` default to the slug / `0.0.0`.

## `apps.py`

```python
from core.plugins import NavItem, PluginAppConfig, PyRunnerPlugin


class MyFlowsConfig(PluginAppConfig):
    name = "plugins.my_flows"     # always "plugins.<slug>"
    label = "my_flows"            # the app label (use the slug)
    plugin = PyRunnerPlugin(
        slug="my_flows",
        name="My Flows",
        version="1.0.0",
        nav_items=[
            NavItem(label="My Flows", url_name="my_flows:index"),
            # icon_svg="<path ... />"     # optional inline SVG <path>; omit for the default
            # superuser_only=True         # hide this item from non-superusers
        ],
    )
```

You subclass `PluginAppConfig` and the natural `from core.plugins import
PluginAppConfig` just works — PyRunner makes sure Django picks *your* config
(not the base) during app discovery. Your `ready()` is wrapped so a registration
error can never crash the site; if you override `ready()`, keep it cheap and
defensive (heavy work belongs in a view or an environment subprocess).

## `urls.py`

```python
from django.urls import path
from . import views

app_name = "my_flows"          # must match the slug

urlpatterns = [
    path("", views.index, name="index"),
]
```

This is auto-mounted at `/plugins/my_flows/`. Reference your routes as
`{% url 'my_flows:index' %}` and in `NavItem(url_name="my_flows:index")`.

## Templates

Extend the console base and include the sidebar so your pages match:

```django
{% extends "base.html" %}
{% block title %}My Flows - PyRunner{% endblock %}
{% block content %}
<div class="flex">
    {% include "cpanel/_sidebar.html" %}
    <div class="flex-1 min-w-0">
        <div class="px-5 lg:px-8 py-7 max-w-[1440px] mx-auto space-y-5">
            <!-- your page -->
        </div>
    </div>
</div>
{% endblock %}
```

Put templates under `templates/<slug>/` to avoid name clashes.

---

## Running real work: `run_in_environment`

Keep the web layer thin. Anything that needs third-party packages must run in a
**PyRunner environment's venv** as an isolated subprocess — never imported into
the Django process. Use the helper:

```python
from core.models import Environment
from core.plugins import run_in_environment

env = Environment.objects.get(name="data-science")
exit_code, stdout, stderr = run_in_environment(env, code="import pandas; print(pandas.__version__)", timeout=30)
# or run a bundled file:
# exit_code, stdout, stderr = run_in_environment(env, path="/app/plugins/my_flows/worker.py", args=["--n", "5"])
```

This reuses the hardened executor path (the env's Python, process-group
isolation, a timeout, captured + size-capped output). A bad package fails the
*call*, not the server. For long jobs, queue a normal `Run` instead.

**Do not** `pip install` into the Django venv or import heavy third-party
packages at module import time in `apps.py`/`models.py`/`views.py`.

---

## Lifecycle (what the admin does)

1. **Upload** the `.zip` (Plugins → Upload). It's validated (zip-slip/size safe)
   and unpacked — its code is *not* imported. Status becomes `Installed`.
2. **Activate** runs `plugin_preflight` in a throwaway subprocess: it imports the
   plugin, applies its migrations, and resolves its URLs against the real DB. On
   success the status becomes `Active`; on failure the error is shown and the
   live site is untouched.
3. **Restart** (a button appears) applies the change — gunicorn and the worker
   re-import the new active set. The entrypoint preflights every active plugin in
   isolation first, so the restart always boots clean.
4. **Deactivate** keeps your data; **Delete** removes the files and row (with an
   optional "drop my tables" data wipe).

If a plugin ever fails to load at boot, it is automatically quarantined as
`Errored` and skipped — the site still boots. As a last resort, boot with
`PYRUNNER_DISABLE_PLUGINS=1` to load zero plugins.

---

## Do / don't

**Do**
- Keep the web layer thin; push compute into `run_in_environment` or a `Run`.
- Namespace templates/static/URLs under your slug.
- Ship migrations if you define models.

**Don't**
- Edit core files or rely on internal core APIs beyond `core.plugins`.
- Import third-party packages into the Django process.
- Do slow or failure-prone work in `ready()`.

A complete, installable sample lives in [`examples/example_plugin/`](../examples/example_plugin/).
