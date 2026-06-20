"""
RunBackend seam (Seam 2).

Extracts "spawn the prepared command + capture results + enforce timeout + kill
the tree" behind a small interface, so the Run lifecycle (status, pid, secret
masking, truncation, the cancel-safe save, temp-file cleanup) stays in
``core.executor`` and the *execution mechanism* becomes swappable.

``LocalSubprocessBackend`` is today's subprocess code moved verbatim and is the
default — with ``PYRUNNER_RUN_BACKEND`` unset, behavior is byte-for-byte
identical to before this seam. Future ``container`` / ``microvm`` backends are
opt-in via the same env var and out of scope here.
"""

import logging
import os

from core.executor_backends.base import (
    RunBackend,
    RunHandle,
    RunResult,
    RunSpec,
)
from core.executor_backends.local import LocalSubprocessBackend

logger = logging.getLogger(__name__)

__all__ = [
    "RunBackend",
    "RunHandle",
    "RunResult",
    "RunSpec",
    "LocalSubprocessBackend",
    "get_run_backend",
]


def get_run_backend() -> RunBackend:
    """Return the configured RunBackend.

    Selected by ``PYRUNNER_RUN_BACKEND`` (default ``local``). An unset or
    ``local`` value yields the byte-for-byte default. An unknown value falls
    back to ``local`` with a warning rather than breaking runs — only the local
    backend exists today, so any other value is a misconfiguration, not a
    reason to fail a user's run.
    """
    name = os.environ.get("PYRUNNER_RUN_BACKEND", "local").strip().lower()
    if name in ("", "local"):
        return LocalSubprocessBackend()
    logger.warning("Unknown PYRUNNER_RUN_BACKEND=%r; falling back to 'local'", name)
    return LocalSubprocessBackend()
