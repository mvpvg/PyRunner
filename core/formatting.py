"""Shared human-readable formatters (dependency-free so any layer can import them)."""


def format_duration(seconds) -> str:
    """Human-readable duration from a number of seconds (``None`` → ``"-"``).

    Shared by ``Run.duration_display`` and ``TaskService._format_duration``, which
    were byte-identical. ``PackageOperation.duration_display`` deliberately keeps its
    own coarser integer format (whole seconds, no hours rollup) and is left separate.
    """
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"
