"""
core.plugins.api — the stable, versioned SDK facade for PyRunner plugins (WS2).

A plugin orchestrates PyRunner primitives (scripts, secrets, datastores,
schedules, environments, runs) THROUGH this module instead of importing
``core.models`` / ``core.tasks`` / ``core.services`` directly. The facade:

  * auto-stamps ownership — every resource a plugin creates carries the plugin's
    ``owner_plugin`` slug AND a ``workspace`` (the two scoping axes from the
    foundations), so it groups, filters, cleans up, and is delete-guarded;
  * is idempotent — ``upsert(...)`` keyed on ``(owner_plugin, owner_key)`` updates
    the same row instead of spawning duplicates on re-provision (the thing the
    qdrant reference plugin hand-rolled via a stored ``script_id``);
  * auto-names DataStores ``"<owner>:<key>"`` so two plugins never collide on the
    globally-/per-workspace-unique ``name`` while referring to a store by a short
    key, and injects owner-scoped secrets under their CLEAN name;
  * never bypasses the run/sandbox seams — runs go through ``queue_script_run``
    (→ RunBackend + ``resolve_isolation``); plugin scripts default to
    ``isolation_mode='inherit'`` so the DB isolation policy decides.

CONTRACT NOTES
- ``owner=None`` selects the LEGACY global/user lane (no ownership stamping), so a
  ported ``upsert_secret(key=...)`` keeps working and old plugins are never forced
  into ``injection_mode='selected'``.
- ``workspace=None`` resolves to the default workspace (the only authoritative,
  request-free source — same rule the scheduler uses).
- Every core import is LAZY (inside a function body), mirroring
  ``run_in_environment``, so importing this module never needs the app registry —
  the light-import boot guard stays intact and a plugin's ``apps.py`` can import
  the SDK without pulling in ``core.models``.
"""

import json

# Plugins declare the SDK version they target in plugin.json (e.g. "2.0"); bump
# on a breaking change to the facade. The wrapped CORE services stay stable.
API_VERSION = "2.0"


# --------------------------------------------------------------------------- #
# Shared helpers (all lazy)
# --------------------------------------------------------------------------- #

def _resolve_workspace(workspace):
    """Return ``workspace`` as-is, or the default workspace when None."""
    if workspace is not None:
        return workspace
    from core.models import Workspace

    return Workspace.get_default()


def _resolve_environment(environment):
    """Accept an Environment instance or a name string; return the instance/None."""
    if environment is None or not isinstance(environment, str):
        return environment
    from core.models import Environment

    return Environment.objects.filter(name=environment).first()


# --------------------------------------------------------------------------- #
# Environments — SELECTED, never owned or created by plugins
# --------------------------------------------------------------------------- #

class EnvironmentAPI:
    """Read-only view of the shared Environments. Plugins SELECT one; they never
    build venvs or pip-install (that work belongs to PyRunner's environment UI)."""

    def list(self):
        from core.models import Environment

        return list(Environment.objects.all().order_by("name"))

    def get(self, name):
        from core.models import Environment

        return Environment.objects.filter(name=name).first()


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #

class SecretAPI:
    """Owner-scoped encrypted secrets. ``owner=None`` ⇒ legacy global/user lane."""

    def __init__(self, owner=None, workspace=None):
        self.owner = owner
        self._workspace = workspace

    def _ws(self):
        return _resolve_workspace(self._workspace)

    def _qs(self):
        from core.models import Secret

        ws = self._ws()
        qs = Secret.objects.filter(workspace=ws)
        if self.owner:
            return qs.filter(owner_plugin=self.owner)
        return qs.filter(owner_plugin__isnull=True)

    def upsert(self, key, value, *, description=None, owner_key=None):
        """Create or update a secret (idempotent on (owner_plugin, key, workspace)).

        The value is always (re-)encrypted. ``key`` is the clean env-var name the
        secret injects under; for an owned secret it is also the idempotency handle.
        """
        from core.models import Secret

        secret = self._qs().filter(key=key).first()
        if secret is None:
            secret = Secret(key=key, workspace=self._ws(), owner_plugin=self.owner)
        secret.owner_key = owner_key if owner_key is not None else (key if self.owner else None)
        if description is not None:
            secret.description = description
        secret.set_value(value)
        secret.save()
        return secret

    def get(self, key):
        return self._qs().filter(key=key).first()

    def list(self):
        return list(self._qs().order_by("key"))

    def grant(self, script, secret, *, active=True):
        """Attach ``secret`` to ``script`` for selected-mode injection (idempotent)."""
        from core.models import SecretGrant

        grant, created = SecretGrant.objects.get_or_create(
            script=script, secret=secret, defaults={"active": active}
        )
        if not created and grant.active != active:
            grant.active = active
            grant.save(update_fields=["active"])
        return grant


# --------------------------------------------------------------------------- #
# Scripts
# --------------------------------------------------------------------------- #

class ScriptAPI:
    """Owner-scoped scripts. ``upsert`` is idempotent on (owner_plugin, owner_key)."""

    def __init__(self, owner=None, workspace=None):
        self.owner = owner
        self._workspace = workspace

    def _ws(self):
        return _resolve_workspace(self._workspace)

    def _qs(self):
        from core.models import Script

        ws = self._ws()
        qs = Script.objects.filter(workspace=ws)
        if self.owner:
            return qs.filter(owner_plugin=self.owner)
        return qs.filter(owner_plugin__isnull=True)

    def upsert(
        self,
        *,
        key=None,
        name=None,
        code=None,
        environment=None,
        timeout_seconds=None,
        injection_mode=None,
        description=None,
        is_enabled=None,
        notify_on=None,
        notify_email=None,
        created_by=None,
    ):
        """Create or update an owned script (idempotent on (owner_plugin, owner_key)).

        ``key`` is the stable per-owner handle (required for owned scripts). The
        human-facing ``name`` is auto-derived from it if omitted. Plugin-owned
        scripts default to ``injection_mode='selected'`` (so they receive only
        granted/same-owner/global secrets); ``isolation_mode`` is left at the model
        default ``'inherit'`` so ``resolve_isolation`` (the sandbox policy) decides.
        """
        from core.models import Script

        if self.owner and not key:
            raise ValueError("ScriptAPI.upsert requires key= for an owned script")

        if self.owner:
            script = self._qs().filter(owner_key=key).first()
        else:
            # Legacy lane: match by name within the workspace (owner-NULL).
            script = self._qs().filter(name=name).first() if name else None

        creating = script is None
        if creating:
            script = Script(workspace=self._ws(), owner_plugin=self.owner, owner_key=key)
            # Default scoped injection for owned scripts; legacy stays 'all'.
            script.injection_mode = injection_mode or (
                Script.InjectionMode.SELECTED if self.owner else Script.InjectionMode.ALL
            )
            if created_by is not None:
                script.created_by = created_by

        script.name = name or script.name or (f"{self.owner}:{key}" if self.owner else key)
        if code is not None:
            script.code = code
        if injection_mode is not None:
            script.injection_mode = injection_mode
        if description is not None:
            script.description = description
        if is_enabled is not None:
            script.is_enabled = is_enabled
        if notify_on is not None:
            script.notify_on = notify_on
        if notify_email is not None:
            script.notify_email = notify_email
        if timeout_seconds is not None:
            script.timeout_seconds = timeout_seconds

        env = _resolve_environment(environment)
        if env is not None:
            script.environment = env
        elif creating:
            raise ValueError(
                "ScriptAPI.upsert requires environment= (instance or name) on create"
            )

        script.save()
        return script

    def set_environment(self, environment):
        """Bulk-set the Environment on EVERY script this plugin owns (one call).

        The "pick a venv on the plugin page, all connected scripts follow"
        behavior. No-op in the legacy (owner=None) lane. Returns the count updated.
        """
        if not self.owner:
            return 0
        env = _resolve_environment(environment)
        if env is None:
            raise ValueError("set_environment: unknown environment")
        return self._qs().update(environment=env)

    def queue_run(self, key, *, triggered_by=None):
        """Queue a tracked Run for the owned script ``key`` (via the RunBackend seam)."""
        from core.models import Run
        from core.tasks import queue_script_run

        script = self.get(key)
        if script is None:
            raise ValueError(f"No owned script with key={key!r}")
        run = Run.objects.create(
            script=script,
            workspace_id=script.workspace_id,
            status=Run.Status.PENDING,
            triggered_by=triggered_by,
            trigger_type=Run.TriggerType.MANUAL,
            code_snapshot=script.code,
        )
        queue_script_run(run)
        return run

    def get(self, key):
        if self.owner:
            return self._qs().filter(owner_key=key).first()
        return self._qs().filter(name=key).first()

    def list(self):
        return list(self._qs().order_by("name"))


# --------------------------------------------------------------------------- #
# DataStores — the plugin's database (no plugin models/migrations)
# --------------------------------------------------------------------------- #

class _OwnedDataStore:
    """Thin dict-ish handle over a DataStore's entries (JSON values), via the ORM."""

    def __init__(self, store):
        self._store = store

    @property
    def name(self):
        return self._store.name

    @property
    def model(self):
        return self._store

    def set(self, key, value):
        from core.models import DataStoreEntry

        DataStoreEntry.objects.update_or_create(
            datastore=self._store, key=key, defaults={"value_json": json.dumps(value)}
        )

    def get(self, key, default=None):
        from core.models import DataStoreEntry

        entry = DataStoreEntry.objects.filter(datastore=self._store, key=key).first()
        return json.loads(entry.value_json) if entry is not None else default

    def all(self):
        from core.models import DataStoreEntry

        return {
            e.key: json.loads(e.value_json)
            for e in DataStoreEntry.objects.filter(datastore=self._store)
        }


class DataStoreAPI:
    """Owner-scoped key-value stores. The stored ``name`` is auto-derived
    ``"<owner>:<key>"`` (owner=None ⇒ the raw key), keeping ``name`` unique while
    the plugin refers to it by a short key."""

    def __init__(self, owner=None, workspace=None):
        self.owner = owner
        self._workspace = workspace

    def _ws(self):
        return _resolve_workspace(self._workspace)

    def _name_for(self, key):
        return f"{self.owner}:{key}" if self.owner else key

    def upsert(self, key, *, description=None, created_by=None):
        """Ensure the owned store exists (idempotent); return a handle for entries."""
        from core.models import DataStore

        name = self._name_for(key)
        store = DataStore.objects.filter(workspace=self._ws(), name=name).first()
        if store is None:
            store = DataStore(name=name, workspace=self._ws())
            if created_by is not None:
                store.created_by = created_by
        store.owner_plugin = self.owner
        store.owner_key = key
        if description is not None:
            store.description = description
        store.save()
        return _OwnedDataStore(store)

    def get(self, key):
        from core.models import DataStore

        store = DataStore.objects.filter(
            workspace=self._ws(), name=self._name_for(key)
        ).first()
        return _OwnedDataStore(store) if store is not None else None

    def list(self):
        from core.models import DataStore

        qs = DataStore.objects.filter(workspace=self._ws())
        qs = qs.filter(owner_plugin=self.owner) if self.owner else qs.filter(
            owner_plugin__isnull=True
        )
        return [_OwnedDataStore(s) for s in qs.order_by("name")]


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #

class ScheduleAPI:
    """Wraps ScriptSchedule + ScheduleService so a plugin schedules a run without
    touching django-q directly (and the global-pause rules still apply)."""

    def __init__(self, owner=None, workspace=None):
        self.owner = owner
        self._workspace = workspace

    def sync(
        self,
        script,
        *,
        mode,
        time_str=None,
        weekday=None,
        interval_minutes=None,
        tz="UTC",
    ):
        """Create/update the script's schedule and push it to django-q2.

        ``mode`` is a ``ScriptSchedule.RunMode`` value ('manual'/'interval'/'daily'
        /'weekly'). Mirrors the hand-rolled qdrant sync_schedule, generalized.
        """
        from core.models import ScriptSchedule
        from core.services.schedule_service import ScheduleService

        sched, _ = ScriptSchedule.objects.get_or_create(
            script=script, defaults={"workspace_id": script.workspace_id}
        )
        sched.run_mode = mode
        sched.timezone = tz or "UTC"
        sched.interval_minutes = None
        sched.daily_times = []
        sched.weekly_days = []
        sched.weekly_times = []
        sched.monthly_days = []
        sched.monthly_times = []

        if mode == ScriptSchedule.RunMode.INTERVAL:
            sched.interval_minutes = int(interval_minutes)
        elif mode == ScriptSchedule.RunMode.DAILY:
            sched.daily_times = [time_str]
        elif mode == ScriptSchedule.RunMode.WEEKLY:
            sched.weekly_days = [int(weekday)]
            sched.weekly_times = [time_str]

        sched.is_active = mode != ScriptSchedule.RunMode.MANUAL
        sched.save()
        ScheduleService.sync_schedule(sched)
        return sched

    def list(self):
        from core.models import ScriptSchedule

        qs = ScriptSchedule.objects.filter(script__workspace=_resolve_workspace(self._workspace))
        if self.owner:
            qs = qs.filter(script__owner_plugin=self.owner)
        return list(qs)
