# -*- coding: utf-8 -*-
"""Entrypoint для health_watchdog (run.sh / supervisord)."""

from __future__ import annotations

import asyncio
import logging

from apps.health_watchdog.main import main_loop
from core.worker_lock import acquire_singleton_lock

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("health_watchdog")
    asyncio.run(main_loop())
