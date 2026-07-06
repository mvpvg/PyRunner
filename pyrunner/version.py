"""
PyRunner version information.
"""

__version__ = "1.13.0"
VERSION = __version__

# Whole-app beta flag. Flip to False to drop every "Beta" badge across the UI
# in one place (badges are rendered via templates/_beta_badge.html, gated on the
# pyrunner_is_beta context flag).
IS_BETA = True


def get_version():
    """Return the current PyRunner version."""
    return __version__
