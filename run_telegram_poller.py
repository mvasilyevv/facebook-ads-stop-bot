# -*- coding: utf-8 -*-
"""Entrypoint для telegram_poller."""

from __future__ import annotations

import asyncio
import logging

from apps.telegram_poller.main import _get_database_url, main_loop
from core.worker_lock import acquire_singleton_lock

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("telegram_poller")
    asyncio.run(main_loop(_get_database_url()))
