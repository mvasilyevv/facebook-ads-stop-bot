# -*- coding: utf-8 -*-
"""Container-compatible entrypoint для FastAPI.

Стартует uvicorn на 0.0.0.0:${API_PORT:-8100}.
"""

from __future__ import annotations

import logging
import os

import uvicorn

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    uvicorn.run(
        "apps.api.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("API_PORT", "8100")),
        reload=False,
        # GET postback AdSet.pro авторизуется query-token'ом. Стандартный
        # uvicorn access logger пишет полный request target и раскрыл бы токен.
        # HTTP-наблюдаемость остаётся в Prometheus middleware и Caddy, где
        # postback route исключён отдельным log_skip.
        access_log=False,
    )
