# -*- coding: utf-8 -*-
"""Run the Meta mutation executor with exclusive access to the money lane."""

from __future__ import annotations

import asyncio
import os

# These values must be established before importing meta_api_worker.main because
# its worker identity and claim lanes are process constants.
os.environ.setdefault("META_API_WORKER_NAME", "autopause")
os.environ.setdefault("META_API_WORKER_LANES", "money")
os.environ.setdefault("META_API_LEASE_SECONDS", "60")

from apps.meta_api_worker.main import main_loop  # noqa: E402

if __name__ == "__main__":
    asyncio.run(main_loop())
