# -*- coding: utf-8 -*-
"""Entrypoint для creator_recorder (run.sh / supervisord)."""

from __future__ import annotations

import asyncio
import logging

from apps.creator_recorder.main import main_loop

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(main_loop())
