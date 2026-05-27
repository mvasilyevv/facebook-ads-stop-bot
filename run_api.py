# -*- coding: utf-8 -*-
"""Entrypoint для FastAPI (run.sh / supervisord).

Стартует uvicorn на 0.0.0.0:8000 — порт и адрес можно менять через переменные
окружения uvicorn'а, но по умолчанию совместимы с k8s service-портом и
docker-compose.
"""

from __future__ import annotations

import logging

import uvicorn

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    uvicorn.run(
        "apps.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
