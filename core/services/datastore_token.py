"""
Stateless per-run token for the internal DataStore API (Seam 1).

Phase A lays the *seam*: the internal datastore endpoint exists and is proven,
but the script-side helper still talks to SQLite directly (byte-for-byte with
today). The full cutover happens in Stage 2 alongside Postgres.

The token is a signed (HMAC-SHA256 over ``SECRET_KEY``) payload that authorizes
datastore access for one run. It is stateless on purpose: the django-q worker
mints it (Stage 2) and the gunicorn web process validates it, two separate
processes that share only ``SECRET_KEY`` via the environment — so no shared DB
row, cache, or in-memory state is required, and the token expires on its own.

Both mint and verify run inside Django (worker / web); the script subprocess is
just an opaque courier of the string, so the dependency-light helper never needs
to import Django to participate.
"""

from django.core import signing

# Namespacing salt so a datastore token can never be cross-used with another
# `django.core.signing` consumer that happens to sign the same payload.
_SALT = "pyrunner.datastore.run-token.v1"

# Default lifetime. Generous enough for a long run, short enough that a leaked
# token is not durably useful. Verification enforces it via ``max_age``.
DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60  # 24h


def mint_datastore_token(run_id) -> str:
    """Mint a signed datastore-access token for a run.

    Args:
        run_id: The Run's id (UUID or str). Stored in the signed payload so the
            endpoint can attribute/scope access in Stage 2.

    Returns:
        An opaque, URL-safe signed token string.
    """
    return signing.dumps({"run_id": str(run_id)}, salt=_SALT)


def verify_datastore_token(token: str, max_age: int = DEFAULT_MAX_AGE_SECONDS):
    """Validate a datastore token and return its payload, or ``None``.

    Never raises: a missing, malformed, tampered, or expired token yields
    ``None`` so callers branch on a simple truthiness check.

    Args:
        token: The signed token string.
        max_age: Maximum token age in seconds before it is considered expired.

    Returns:
        The decoded payload dict (e.g. ``{"run_id": "..."}``) or ``None``.
    """
    if not token:
        return None
    try:
        return signing.loads(token, salt=_SALT, max_age=max_age)
    except signing.BadSignature:
        # Covers SignatureExpired (a BadSignature subclass) and tampering.
        return None
    except Exception:
        # Defensive: malformed input must never surface as a 500.
        return None
