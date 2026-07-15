"""
Timezone parsing shared by scheduling, backups, and display.

One place for the "IANA name -> ZoneInfo, warn + fall back to UTC on a bad
value" rule, so the three consumers (schedule crons, the backup schedule, and
the display middleware) can never drift on how a garbage value degrades.
"""

import logging
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")


def safe_zoneinfo(name: str, *, context: str = "") -> ZoneInfo:
    """ZoneInfo for ``name``; UTC (with a warning) when the name is invalid."""
    cleaned = (name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(cleaned)
    except Exception:
        suffix = f" ({context})" if context else ""
        logger.warning(f"Unknown timezone '{cleaned}'{suffix} - falling back to UTC")
        return UTC
