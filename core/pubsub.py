"""Redis wake-up channels whose durable state lives in PostgreSQL.

These notifications are accelerators only. Consumers must always reconcile
the corresponding PostgreSQL rows after waking.
"""

CHANNEL_TRACKER_WAKEUP = "fb_agent:tracker:wakeup"

__all__ = ["CHANNEL_TRACKER_WAKEUP"]
