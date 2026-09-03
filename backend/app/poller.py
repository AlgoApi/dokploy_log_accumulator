from __future__ import annotations

from app.log_streams import get_stream_manager


def run_poll_cycle() -> dict:
    """Refresh log stream workers (legacy name for API compatibility)."""
    return get_stream_manager().sync_now()
