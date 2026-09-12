"""Scheduled DNS change package.

SQLite stores only intent (what to do, when) — never zone state.
"""

from dns_zone_manager.scheduler.store import ScheduledChangeStore

__all__ = ["ScheduledChangeStore"]
