# -*- coding: utf-8 -*-
"""Точка входа для запуска meta_api_worker как отдельного процесса."""

from __future__ import annotations

import asyncio

from apps.meta_api_worker.main import main

if __name__ == "__main__":
    asyncio.run(main())
