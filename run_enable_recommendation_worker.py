# -*- coding: utf-8 -*-
"""Entrypoint для enable_recommendation_worker."""

from __future__ import annotations

import asyncio
import logging

from apps.enable_recommendation_worker.main import main_loop
from core.config import get_settings
from core.worker_lock import acquire_singleton_lock, run_postgres_singleton
from core.worker_metrics import start_worker_metrics_server

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    acquire_singleton_lock("enable_reco")
    start_worker_metrics_server("enable_reco")
    database_url = get_settings().database_url
    asyncio.run(
        run_postgres_singleton(
            "enable_recommendation",
            main_loop,
            database_url=database_url,
        )
    )
