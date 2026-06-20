"""
Seam 2 — RunBackend golden tests.

Prove the extract-behind-an-interface refactor produces the SAME Run row as the
pre-seam code for the representative cases the plan names: success, non-zero
exit, timeout (tree killed + drained), secret masking, and a force-stop landing
mid-run (the cancel-safe conditional update must win). With the default backend
(``PYRUNNER_RUN_BACKEND`` unset) this is today's behavior, byte-for-byte.

The environment is stubbed to the test runner's own Python so we exercise the
REAL subprocess spawn + backend + lifecycle without needing a venv on disk.
"""

import sys
import uuid
from unittest import mock

from django.test import TestCase

from core.executor import execute_run, run_in_environment, _kill_process_tree
from core.executor_backends import (
    LocalSubprocessBackend,
    RunHandle,
    RunResult,
    RunSpec,
    get_run_backend,
)
from core.executor_backends.base import RunBackend
from core.models import Environment, Run, Script


def _make_run(code: str, timeout: int = 3600) -> Run:
    env = Environment.objects.create(name="t", path=f"env{uuid.uuid4().hex[:10]}")
    script = Script.objects.create(
        name="s", code=code, environment=env, timeout_seconds=timeout
    )
    return Run.objects.create(script=script, status=Run.Status.PENDING)


def _stub_python(func):
    """Decorator: make _validate_environment return the test runner's Python."""
    return mock.patch("core.executor._validate_environment", return_value=sys.executable)(func)


class RunBackendSelectorTests(TestCase):
    def test_default_is_local(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("PYRUNNER_RUN_BACKEND", None)
            self.assertIsInstance(get_run_backend(), LocalSubprocessBackend)

    def test_explicit_local(self):
        with mock.patch.dict("os.environ", {"PYRUNNER_RUN_BACKEND": "local"}):
            self.assertIsInstance(get_run_backend(), LocalSubprocessBackend)

    def test_unknown_falls_back_to_local(self):
        with mock.patch.dict("os.environ", {"PYRUNNER_RUN_BACKEND": "warpdrive"}):
            self.assertIsInstance(get_run_backend(), LocalSubprocessBackend)

    def test_kill_process_tree_still_importable_from_executor(self):
        # TaskService.force_stop_task depends on this import path.
        self.assertTrue(callable(_kill_process_tree))


class ExecuteRunGoldenTests(TestCase):
    @_stub_python
    def test_success(self, _val):
        run = _make_run("print('hello world')")
        execute_run(run)
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.SUCCESS)
        self.assertEqual(run.exit_code, 0)
        self.assertIn("hello world", run.stdout)
        self.assertEqual(run.stderr, "")
        self.assertIsNone(run.pid)  # cleared on completion
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.ended_at)

    @_stub_python
    def test_non_zero_exit(self, _val):
        run = _make_run("import sys; sys.exit(3)")
        execute_run(run)
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.FAILED)
        self.assertEqual(run.exit_code, 3)
        self.assertIsNone(run.pid)

    @_stub_python
    def test_timeout_kills_and_marks(self, _val):
        run = _make_run("import time; time.sleep(30)", timeout=1)
        execute_run(run)
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.TIMEOUT)
        self.assertEqual(run.exit_code, -1)
        self.assertIn("[TIMEOUT", run.stderr)
        self.assertIsNone(run.pid)

    @_stub_python
    @mock.patch(
        "core.executor._get_secrets_env",
        return_value={"MY_SECRET": "supersecretvalue12"},
    )
    def test_secret_value_is_masked(self, _secrets, _val):
        run = _make_run("import os; print(os.environ['MY_SECRET'])")
        execute_run(run)
        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.SUCCESS)
        self.assertNotIn("supersecretvalue12", run.stdout)
        self.assertIn("[MY_SECRET:MASKED]", run.stdout)

    @_stub_python
    def test_force_stop_midrun_preserves_cancelled(self, _val):
        """A force-stop landing during execution must not be clobbered by the
        executor's finally-block save (the cancel-safe conditional update)."""
        run = _make_run("print('ignored')")

        class _CancelMidRun(RunBackend):
            def start(self, spec):
                return RunHandle(pid=999999, native=None)

            def wait(self, handle, timeout):
                # Simulate TaskService.force_stop_task flipping status mid-run.
                Run.objects.filter(pk=run.pk).update(status=Run.Status.CANCELLED)
                return RunResult(exit_code=0, stdout="done", stderr="", timed_out=False)

            def kill(self, handle):
                pass

        with mock.patch("core.executor.get_run_backend", return_value=_CancelMidRun()):
            execute_run(run)

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.CANCELLED)  # not overwritten to SUCCESS
        self.assertIsNone(run.pid)


class _FakeEnv:
    """Minimal stand-in: run_in_environment only needs these three."""

    def exists(self):
        return True

    def get_python_executable(self):
        return sys.executable

    def get_full_path(self):
        return "/fake"


class RunInEnvironmentTests(TestCase):
    def test_success_tuple(self):
        code, out, err = run_in_environment(_FakeEnv(), code="print('x')", timeout=30)
        self.assertEqual(code, 0)
        self.assertIn("x", out)
        self.assertEqual(err, "")

    def test_nonzero_tuple(self):
        code, out, err = run_in_environment(
            _FakeEnv(), code="import sys; sys.exit(5)", timeout=30
        )
        self.assertEqual(code, 5)

    def test_timeout_tuple(self):
        code, out, err = run_in_environment(
            _FakeEnv(), code="import time; time.sleep(30)", timeout=1
        )
        self.assertEqual(code, -1)
        self.assertIn("[TIMEOUT: exceeded 1s]", err)
