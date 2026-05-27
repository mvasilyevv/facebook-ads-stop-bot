# -*- coding: utf-8 -*-
"""Entrypoint для observer_worker v2."""

from __future__ import annotations

import asyncio
import logging

from apps.observer_worker_v2.main import main_loop

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(main_loop())
