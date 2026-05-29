# -*- coding: utf-8 -*-
"""Entrypoint для tracker_aggregator_worker (run.sh / supervisord)."""

from __future__ import annotations

import asyncio
import logging

from apps.tracker_aggregator_worker.main import _get_database_url, main_loop

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(main_loop(_get_database_url()))
