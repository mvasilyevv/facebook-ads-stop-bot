# -*- coding: utf-8 -*-
"""Durable per-worker liveness for the operator snapshot (issue #176).

Until this revision a worker's heartbeat existed only as a process-local
Prometheus gauge (``core/worker_metrics.py``) — invisible to the operator
snapshot, which must read state from PostgreSQL. 18.08.2026 the campaign
creation worker did not drain its queue for eleven hours; two launches died
against their deadline before starting, and nothing on the operator screen
changed. This table gives every background worker (not the per-cabinet scan
actors already in ``cabinet_runtime``) a durable liveness row so a stopped
worker is visible before its consequences are.

Two columns on purpose, not one:

- ``last_heartbeat_at`` — the worker's process is alive (its liveness loop
  still ticks), independent of whether it is doing anything useful.
- ``last_poll_success_at`` — the worker's real work loop (the one that claims
  tasks or runs its scheduled check) completed an iteration. A worker with an
  empty queue keeps advancing this and stays healthy; a worker whose work loop
  hangs stops advancing it while a decoupled heartbeat coroutine may keep
  ticking regardless — the exact gap that hid the 18.08 incident.

The table starts empty: a worker's own process writes its first row on the
next heartbeat tick, so there is no stale or invented data to backfill.
"""

from __future__ import annotations

from alembic import op

revision = "0009_worker_heartbeats"
down_revision = "0008_account_status_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE public.worker_heartbeats (
            worker_name text PRIMARY KEY,
            last_heartbeat_at timestamp with time zone NOT NULL,
            last_poll_success_at timestamp with time zone
        )
        """
    )


def downgrade() -> None:
    raise RuntimeError("worker heartbeat migration is forward-only")
