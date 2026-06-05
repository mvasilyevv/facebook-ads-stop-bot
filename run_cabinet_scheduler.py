# -*- coding: utf-8 -*-
"""Entrypoint для cabinet_scheduler (run.sh / supervisord)."""

from __future__ import annotations

import asyncio
import logging

from apps.cabinet_scheduler.main import main_loop
from core.worker_lock import acquire_singleton_lock

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("cabinet_scheduler")
    asyncio.run(main_loop())
