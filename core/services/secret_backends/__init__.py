"""
Secret-backend provider package — external secrets-manager adapters.

Importing this package registers all built-in backends, so callers only need
``from core.services.secret_backends import get_backend, resolve_secret_ref``.
"""

from .base import (
    SecretBackend,
    SecretResolutionError,
    get_backend,
    list_backends,
    register,
)
from .cache import clear_cache, resolve_secret_ref

# Side-effect imports: register the built-in backends on import.
from . import vault  # noqa: F401  (backend registration)
from . import aws_sm  # noqa: F401  (backend registration)
from . import infisical  # noqa: F401  (backend registration)
from . import doppler  # noqa: F401  (backend registration)
from . import custom  # noqa: F401  (backend registration)

__all__ = [
    "SecretBackend",
    "SecretResolutionError",
    "get_backend",
    "list_backends",
    "register",
    "resolve_secret_ref",
    "clear_cache",
]
