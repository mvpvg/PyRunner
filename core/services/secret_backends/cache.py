"""
In-process TTL cache + resolution orchestration for external secrets.

**NEVER ``django.core.cache``.** DatabaseCache / Redis would persist decrypted
secret VALUES at rest — the one foot-gun this feature must not have. The cache is
a module-level dict living only in the resolving process (each gunicorn worker
keeps its own; a fetch per worker per TTL is cheap). Reviewers should reject any
patch that reaches for the shared Django cache here.

Keyed on ``(profile_id, path)`` with a per-path lock so a TTL expiry doesn't
stampede every concurrent run into the provider at once (thundering herd).
"""

import logging
import threading
import time

from .base import SecretResolutionError, get_backend

logger = logging.getLogger(__name__)

# (profile_id, path) -> (values_dict, fetched_at_monotonic). In-process ONLY.
_cache: dict[tuple[str, str], tuple[dict[str, str], float]] = {}
_cache_lock = threading.Lock()  # guards _cache AND _path_locks
_path_locks: dict[tuple[str, str], threading.Lock] = {}


def clear_cache() -> None:
    """Drop all cached values and per-path locks (used by tests)."""
    with _cache_lock:
        _cache.clear()
        _path_locks.clear()


def resolve_secret_ref(profile, ref: str) -> str:
    """Resolve one external secret reference to its plaintext value.

    Splits the ref into ``(path, key)`` via the adapter, fetches the whole path
    (cached per ``(profile, path)`` for ``profile.cache_ttl`` seconds), then
    extracts the key. Raises ``SecretResolutionError`` on any failure — unless
    ``profile.on_error == "use_stale"`` and a cached value exists.
    """
    backend = get_backend(profile.provider_type)
    path, key = backend.split_ref(ref)
    values = _fetch_path(profile, backend, path)
    return _extract_key(values, key, path)


def _extract_key(values: dict, key: str, path: str) -> str:
    if key:
        if key not in values:
            raise SecretResolutionError(
                f"key {key!r} not found at {path!r} (available: {sorted(values)})"
            )
        return values[key]
    # No explicit '#key': only unambiguous when the path holds exactly one value
    # (e.g. single-value backends). Otherwise the caller must name a key.
    if len(values) == 1:
        return next(iter(values.values()))
    raise SecretResolutionError(
        f"reference {path!r} needs a '#key' — the path holds {len(values)} values"
    )


def _is_fresh(entry, ttl: int, now: float) -> bool:
    if entry is None or ttl <= 0:
        return False
    return (now - entry[1]) < ttl


def _get_path_lock(cache_key) -> threading.Lock:
    with _cache_lock:
        lock = _path_locks.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _path_locks[cache_key] = lock
        return lock


def _fetch_path(profile, backend, path: str) -> dict:
    ttl = profile.cache_ttl or 0
    cache_key = (str(profile.id), path)

    if ttl > 0:
        with _cache_lock:
            entry = _cache.get(cache_key)
        if _is_fresh(entry, ttl, time.monotonic()):
            return entry[0]

    # Serialize live fetches for this path so concurrent runs don't all stampede
    # the provider when the TTL expires.
    with _get_path_lock(cache_key):
        # Re-check: another thread may have refreshed while we waited for the lock.
        if ttl > 0:
            with _cache_lock:
                entry = _cache.get(cache_key)
            if _is_fresh(entry, ttl, time.monotonic()):
                return entry[0]
        try:
            values = backend.fetch(profile, path)
        except Exception as exc:
            return _on_fetch_error(profile, cache_key, path, exc)
        if ttl > 0:
            with _cache_lock:
                _cache[cache_key] = (values, time.monotonic())
        return values


def _on_fetch_error(profile, cache_key, path: str, exc: Exception) -> dict:
    """Apply the profile's ``on_error`` policy to a failed live fetch."""
    if profile.on_error == "use_stale":
        with _cache_lock:
            entry = _cache.get(cache_key)
        if entry is not None:
            logger.warning(
                "Serving STALE secret for provider %r path %r (live fetch failed: %s)",
                profile.name,
                path,
                exc,
            )
            return entry[0]
    if isinstance(exc, SecretResolutionError):
        raise exc
    raise SecretResolutionError(str(exc)) from exc
