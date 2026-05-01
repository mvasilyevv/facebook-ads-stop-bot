# -*- coding: utf-8 -*-
"""Точка входа health_watchdog."""

from __future__ import annotations

import asyncio

from apps.health_watchdog.main import main

if __name__ == "__main__":
    asyncio.run(main())
