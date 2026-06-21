"""
Sandbox Stage 2b — SandboxedSubprocessBackend (fs/network isolation layer).

The backend wraps the script command in bwrap/nsjail when the host can sandbox,
and degrades to the plain local spawn (still with rlimits) when it can't — so
selecting it never breaks a run. The actual nsjail/bwrap *execution* needs a
userns-capable host (validated on the real stack, where the default Docker
profile blocks userns → this backend degrades to rlimits-only). These tests
cover what is host-independent: command construction, the datastore env-drop,
the selector, and that the degrade path actually runs a script here.
"""

import os
import sys
import uuid
from unittest import mock

from django.test import TestCase

from core.executor import execute_run
from core.executor_backends import (
    LocalSubprocessBackend,
    RunHandle,
    RunSpec,
    get_run_backend,
)
from core.executor_backends import sandboxed
from core.executor_backends.sandboxed import (
    SandboxedSubprocessBackend,
    build_bwrap_argv,
    build_nsjail_argv,
    reset_runtime_tier,
)
from core.models import Environment, Run, Script


def _make_run(code: str, timeout: int = 3600) -> Run:
    env = Environment.objects.create(name="t", path=f"env{uuid.uuid4().hex[:10]}")
    script = Script.objects.create(
        name="s", code=code, environment=env, timeout_seconds=timeout
    )
    return Run.objects.create(script=script, status=Run.Status.PENDING)


def _spec(**over):
    base = dict(
        cmd=["/opt/venv/bin/python", "/work/script.py"],
        env={
            "PYRUNNER_DB_PATH": "/app/data/db.sqlite3",
            "PYRUNNER_INTERNAL_URL": "http://127.0.0.1:8000",
            "PYRUNNER_INTERNAL_TOKEN": "tok",
            "PYTHONPATH": "/app/core/script_helpers",
        },
        cwd="/work",
    )
    base.update(over)
    return RunSpec(**base)


class SelectorTests(TestCase):
    def test_sandbox_value_selects_sandboxed_backend(self):
        with mock.patch.dict("os.environ", {"PYRUNNER_RUN_BACKEND": "sandbox"}):
            self.assertIsInstance(get_run_backend(), SandboxedSubprocessBackend)

    def test_default_still_local(self):
        with mock.patch.dict("os.environ", {"PYRUNNER_RUN_BACKEND": "local"}):
            self.assertIsInstance(get_run_backend(), LocalSubprocessBackend)


class RoBindDirsTests(TestCase):
    def test_includes_system_venv_and_pythonpath_excludes_workdir(self):
        spec = _spec(env={"PYTHONPATH": "/app/helpers" + os.pathsep + "/work"})
        with mock.patch.object(sandboxed.os.path, "isdir", return_value=True):
            dirs = sandboxed._ro_bind_dirs(spec)
        self.assertIn("/usr", dirs)
        self.assertIn("/etc", dirs)
        self.assertIn("/opt/venv", dirs)       # venv = two levels up from python
        self.assertIn("/app/helpers", dirs)
        self.assertNotIn("/work", dirs)         # the writable workdir is bound rw, not ro

    def test_skips_missing_dirs(self):
        spec = _spec()
        with mock.patch.object(sandboxed.os.path, "isdir", side_effect=lambda d: d == "/usr"):
            dirs = sandboxed._ro_bind_dirs(spec)
        self.assertEqual(dirs, ["/usr"])


class ArgvConstructionTests(TestCase):
    def test_bwrap_argv_structure(self):
        with mock.patch.object(sandboxed.os.path, "isdir", return_value=True):
            argv = build_bwrap_argv("/usr/bin/bwrap", _spec())
        self.assertEqual(argv[0], "/usr/bin/bwrap")
        self.assertIn("--unshare-user", argv)
        self.assertIn("--die-with-parent", argv)
        # workdir bound writable + chdir'd
        self.assertIn("--bind", argv)
        self.assertIn("--chdir", argv)
        # network NOT unshared (loopback datastore + egress work)
        self.assertNotIn("--unshare-net", argv)
        # original command is the tail after the `--` separator
        self.assertEqual(argv[-2:], ["/opt/venv/bin/python", "/work/script.py"])
        self.assertEqual(argv[argv.index("--") + 1:], ["/opt/venv/bin/python", "/work/script.py"])

    def test_nsjail_argv_structure(self):
        with mock.patch.object(sandboxed.os.path, "isdir", return_value=True):
            argv = build_nsjail_argv("/usr/bin/nsjail", _spec())
        self.assertEqual(argv[0], "/usr/bin/nsjail")
        self.assertIn("-Mo", argv)
        self.assertIn("--disable_clone_newnet", argv)  # share net
        self.assertEqual(argv[-2:], ["/opt/venv/bin/python", "/work/script.py"])


class WrapAndDegradeTests(TestCase):
    def setUp(self):
        reset_runtime_tier()
        self.addCleanup(reset_runtime_tier)

    def _capture_local_start(self):
        captured = {}

        def fake_start(inner_self, spec):
            captured["spec"] = spec
            return RunHandle(pid=123)

        return captured, fake_start

    def test_degrades_to_plain_when_not_full(self):
        captured, fake_start = self._capture_local_start()
        backend = SandboxedSubprocessBackend()
        spec = _spec()
        with mock.patch.object(sandboxed, "runtime_tier", return_value="rlimits_only"), \
             mock.patch.object(LocalSubprocessBackend, "start", fake_start):
            backend.start(spec)
        # The original, UNWRAPPED spec runs (cmd unchanged, DB_PATH retained —
        # no sandbox to force the API path).
        self.assertEqual(captured["spec"].cmd, spec.cmd)
        self.assertIn("PYRUNNER_DB_PATH", captured["spec"].env)

    def test_wraps_and_drops_db_path_when_full(self):
        captured, fake_start = self._capture_local_start()
        backend = SandboxedSubprocessBackend()
        spec = _spec()
        with mock.patch.object(sandboxed, "runtime_tier", return_value="full"), \
             mock.patch.object(sandboxed.shutil, "which",
                               side_effect=lambda t: "/usr/bin/bwrap" if t == "bwrap" else None), \
             mock.patch.object(sandboxed.os.path, "isdir", return_value=True), \
             mock.patch.object(LocalSubprocessBackend, "start", fake_start):
            backend.start(spec)
        wrapped = captured["spec"]
        self.assertEqual(wrapped.cmd[0], "/usr/bin/bwrap")
        self.assertEqual(wrapped.cmd[-2:], spec.cmd)            # original cmd wrapped
        self.assertNotIn("PYRUNNER_DB_PATH", wrapped.env)       # forced to loopback API
        self.assertIn("PYRUNNER_INTERNAL_URL", wrapped.env)     # API reach preserved
        self.assertEqual(wrapped.limits, spec.limits)           # rlimits floor carried through

    def test_falls_back_when_full_but_no_tool(self):
        captured, fake_start = self._capture_local_start()
        backend = SandboxedSubprocessBackend()
        spec = _spec()
        with mock.patch.object(sandboxed, "runtime_tier", return_value="full"), \
             mock.patch.object(sandboxed.shutil, "which", return_value=None), \
             mock.patch.object(LocalSubprocessBackend, "start", fake_start):
            backend.start(spec)
        self.assertEqual(captured["spec"].cmd, spec.cmd)  # no tool -> plain


class EndToEndDegradeTests(TestCase):
    """Selecting the sandbox backend on a host that can't sandbox (this Windows
    dev box / the default-Docker stack) must still run scripts — degrade, never
    break."""

    def setUp(self):
        reset_runtime_tier()
        self.addCleanup(reset_runtime_tier)

    def test_sandbox_backend_runs_script_via_degrade(self):
        run = _make_run("print('hello sandbox')")
        with mock.patch("core.executor._validate_environment", return_value=sys.executable), \
             mock.patch.dict("os.environ", {"PYRUNNER_RUN_BACKEND": "sandbox"}):
            execute_run(run)
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.SUCCESS)
        self.assertIn("hello sandbox", run.stdout)
