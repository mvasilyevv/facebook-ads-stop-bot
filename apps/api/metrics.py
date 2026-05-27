# -*- coding: utf-8 -*-
"""Prometheus counters/histograms для FastAPI.

Метрики создаются один раз при импорте модуля (singleton): default REGISTRY
пакета prometheus_client. Это позволяет `generate_latest()` отдавать всё разом.

Кардинальность лейбла `path` контролируется в middleware: используется
route template (`/api/v1/postback/adsetpro`), а не raw URL — иначе при
dynamic segments счётчик взорвался бы.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

REQUESTS_TOTAL = Counter(
    "app_requests_total",
    "Общее количество HTTP-запросов",
    ["path", "method", "status"],
)

REQUEST_DURATION = Histogram(
    "app_request_duration_seconds",
    "Время обработки HTTP-запроса",
    ["path", "method"],
)
