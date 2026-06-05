# -*- coding: utf-8 -*-
"""Entrypoint для observer_worker."""

from __future__ import annotations

import asyncio
import logging

from apps.observer_worker.main import main_loop
from core.worker_lock import acquire_singleton_lock

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("observer")
    asyncio.run(main_loop())
