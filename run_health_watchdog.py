# -*- coding: utf-8 -*-
"""Entrypoint для health_watchdog (run.sh / supervisord)."""

from __future__ import annotations

import asyncio
import logging

from apps.health_watchdog.main import main_loop

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(main_loop())
