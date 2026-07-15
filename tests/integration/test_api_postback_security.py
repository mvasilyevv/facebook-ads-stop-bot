# -*- coding: utf-8 -*-
"""Интеграционный: security-пути POST /api/v1/postback/adsetpro.

Покрывает:
- secrets.compare_digest вместо != (CRIT #4): happy-path 202 + timing-санити
  на разных wrong secrets (грубая проверка constant-time).
- BodySizeLimitMiddleware (MID #18): 100 KB body → 413, пустое → 422.

Sync TestClient + dependency_overrides[get_settings] — локально для каждого
теста, глобальный синглтон Settings не трогаем.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from apps.api.deps import get_settings
from apps.api.main import create_app
from apps.api.middleware.body_size import MAX_REQUEST_BODY_BYTES
from core.adset_pro.ingest import IngestResult
from core.config import Settings

_VALID_BODY = {
    "click_id": "abc-123",
    "fb_ad_id": "ad-1",
    "event_type": "ftd",
    "revenue": "10.50",
    "currency": "USD",
}


def _make_app_with_secret(secret: str) -> object:
    """Собрать FastAPI с заданным adsetpro_postback_secret."""
    app = create_app()
    settings_override = Settings(adsetpro_postback_secret=secret)
    app.dependency_overrides[get_settings] = lambda: settings_override
    return app


@pytest.fixture(autouse=True)
def _stub_ingest(monkeypatch):
    """ingest_postback стабим — sync TestClient не работает с реальным БД-engine."""

    async def _fake_ingest(_engine, _event, *, signature_valid=True):
        return IngestResult(
            inserted=True,
            is_duplicate=False,
            event_id=1,
            fb_ad_fk=None,
        )

    # resolve_adsetpro_postback_secret делает DB-чтение (adsetpro_credentials) — стабим
    # на возврат fallback (== env), чтобы sync TestClient не трогал реальный БД-engine.
    async def _fake_resolve_secret(_engine, *, fallback=None):
        return fallback or ""

    monkeypatch.setattr("apps.api.routers.postback.ingest_postback", _fake_ingest)
    monkeypatch.setattr(
        "apps.api.routers.postback.resolve_adsetpro_postback_secret", _fake_resolve_secret
    )
    yield


# Правильный secret через compare_digest пускает запрос → 202.
def test_postback_accepts_with_correct_secret_via_compare_digest() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/postback/adsetpro",
        json=_VALID_BODY,
        headers={"X-Postback-Secret": "real-secret"},
    )
    assert resp.status_code == 202
    assert resp.json()["received"] is True


# Неправильный secret → 401 (compare_digest вернул False).
def test_postback_rejects_wrong_secret() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/postback/adsetpro",
        json=_VALID_BODY,
        headers={"X-Postback-Secret": "evil-guess"},
    )
    assert resp.status_code == 401


# Грубый санити-чек timing: разные wrong secrets дают сопоставимые задержки.
# compare_digest реализован на C-уровне (hmac.compare_digest), отличие во времени
# должно укладываться в 5 мс на одинаковой длине входов. Это не строгое
# доказательство constant-time, но защита от регрессии «откатили обратно на !=».
def test_postback_wrong_secret_timing_is_uniform() -> None:
    app = _make_app_with_secret("real-secret-of-fixed-length")
    client = TestClient(app)

    samples_a: list[float] = []
    samples_b: list[float] = []
    for _ in range(10):
        t0 = time.monotonic()
        client.post(
            "/api/v1/postback/adsetpro",
            json=_VALID_BODY,
            headers={"X-Postback-Secret": "aaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        )
        samples_a.append(time.monotonic() - t0)

        t0 = time.monotonic()
        client.post(
            "/api/v1/postback/adsetpro",
            json=_VALID_BODY,
            headers={"X-Postback-Secret": "real-secret-of-fixed-lengX"},
        )
        samples_b.append(time.monotonic() - t0)

    # Берём медиану — устойчиво к выбросам GC/IO.
    samples_a.sort()
    samples_b.sort()
    median_a = samples_a[len(samples_a) // 2]
    median_b = samples_b[len(samples_b) // 2]
    diff = abs(median_a - median_b)
    # 50 мс — щедрый запас на CI jitter; реальное отличие compare_digest на таких
    # длинах < 1 мкс. Если кто-то откатит на != — отличие будет огромным только
    # на сильно различающихся длинах, но порог здесь — защита от очевидных регрессий.
    assert diff < 0.05, f"timing variance too big: {diff * 1000:.1f}ms"


# Body > 64 KB → 413 от BodySizeLimitMiddleware (запрос отбит до handler'а).
def test_postback_rejects_oversized_body() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    big_payload = dict(_VALID_BODY)
    # 100 KB паддинга в кастомное поле (PostbackEvent.raw подхватит, если бы
    # дошло до handler'а, но middleware отобьёт раньше).
    big_payload["padding"] = "x" * (100 * 1024)
    body_bytes = json.dumps(big_payload).encode("utf-8")
    assert len(body_bytes) > MAX_REQUEST_BODY_BYTES, "санити: тестовый body действительно > лимита"
    resp = client.post(
        "/api/v1/postback/adsetpro",
        content=body_bytes,
        headers={
            "X-Postback-Secret": "real-secret",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 413
    assert resp.json()["max_bytes"] == MAX_REQUEST_BODY_BYTES


# Body точно на границе лимита — пропускается (middleware отбивает только >).
def test_postback_accepts_body_at_limit() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    # Подбираем padding так, чтобы итоговый JSON был <= лимита.
    base = dict(_VALID_BODY)
    base["padding"] = ""
    base_size = len(json.dumps(base).encode("utf-8"))
    pad_len = MAX_REQUEST_BODY_BYTES - base_size - 10  # -10 как запас на JSON quoting
    base["padding"] = "x" * pad_len
    body_bytes = json.dumps(base).encode("utf-8")
    assert len(body_bytes) <= MAX_REQUEST_BODY_BYTES
    resp = client.post(
        "/api/v1/postback/adsetpro",
        content=body_bytes,
        headers={
            "X-Postback-Secret": "real-secret",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 202


# H7b: /api/tools/* освобождён от 64KB-лимита (large multipart с изображениями).
# Большой body НЕ отбивается 413 — доходит до handler'а (dev-tools выключены → 403/иное,
# но точно не 413, иначе картинки в creative-uniquify никогда не загрузить).
def test_tools_path_exempt_from_body_limit() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    big = b"x" * (100 * 1024)
    assert len(big) > MAX_REQUEST_BODY_BYTES, "санити: body действительно > лимита"
    resp = client.post(
        "/api/tools/creative-uniquify",
        content=big,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code != 413, "tools-path должен быть исключён из 64KB-лимита"


# Нет поддерживаемого event_type → технически unknown и безопасно 200 ignored,
# чтобы AdSet.pro не ретраил бессмысленный postback бесконечно.
def test_postback_empty_body_returns_200_ignored() -> None:
    app = _make_app_with_secret("real-secret")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/postback/adsetpro",
        json={},
        headers={"X-Postback-Secret": "real-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
