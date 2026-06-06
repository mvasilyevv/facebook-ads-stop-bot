# -*- coding: utf-8 -*-
"""Unit: _extract_client_key для /ai/analyze rate-limit (H7a).

XFF доверяется только за доверенным прокси (trust_proxy=True). По умолчанию ключ —
реальный TCP-peer, чтобы подделкой X-Forwarded-For нельзя было обойти IP-rate-limit
и жечь AI-бюджет от чужого имени.
"""

from __future__ import annotations

from types import SimpleNamespace

from apps.api.routers.v1.ai_analyze import _extract_client_key


class _FakeRequest:
    def __init__(self, xff: str | None, peer: str | None) -> None:
        self.headers = {"X-Forwarded-For": xff} if xff else {}
        self.client = SimpleNamespace(host=peer) if peer else None


# По умолчанию (trust_proxy=False) XFF игнорируется → берётся реальный peer
def test_client_key_ignores_xff_by_default() -> None:
    req = _FakeRequest(xff="1.2.3.4", peer="10.0.0.9")
    assert _extract_client_key(req) == "10.0.0.9"


# Подделанный XFF не подменяет ключ: оба запроса с одного peer → один rate-limit ключ
def test_client_key_spoofed_xff_no_bypass() -> None:
    req1 = _FakeRequest(xff="9.9.9.9", peer="10.0.0.9")
    req2 = _FakeRequest(xff="8.8.8.8", peer="10.0.0.9")
    assert _extract_client_key(req1) == _extract_client_key(req2) == "10.0.0.9"


# За доверенным прокси (trust_proxy=True) берётся первый (левый) IP из XFF
def test_client_key_trusts_xff_when_enabled() -> None:
    req = _FakeRequest(xff="1.2.3.4, 10.0.0.1", peer="10.0.0.9")
    assert _extract_client_key(req, trust_proxy=True) == "1.2.3.4"


# Нет client и нет XFF → 'unknown'
def test_client_key_unknown_fallback() -> None:
    req = _FakeRequest(xff=None, peer=None)
    assert _extract_client_key(req) == "unknown"
