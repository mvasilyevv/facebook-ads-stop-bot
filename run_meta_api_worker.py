# -*- coding: utf-8 -*-
"""Entrypoint для meta_api_worker (run.sh / supervisord)."""

from __future__ import annotations

import asyncio
import logging

from apps.meta_api_worker.main import main_loop
from core.worker_lock import acquire_singleton_lock

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("meta_api")
    asyncio.run(main_loop())
