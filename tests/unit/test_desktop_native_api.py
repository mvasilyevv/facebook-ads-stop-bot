# -*- coding: utf-8 -*-
"""GET /api/desktop/native — данные нативного канала к столу.

Веб-канала с билетами больше нет; клиент RustDesk настраивается один раз, и
всё, что для этого нужно — адрес брокера, его публичный ключ и ID стола, —
публикует сам стол, а этот эндпоинт отдаёт владельцу. Пароль канала сюда не
попадает никогда.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import apps.api.routers.v1.desktop as m


def _settings(path: Path) -> SimpleNamespace:
    return SimpleNamespace(desktop_native_channel_path=path)


def test_channel_info_reads_what_the_desktop_published(tmp_path: Path) -> None:
    path = tmp_path / "rustdesk.json"
    path.write_text(
        json.dumps(
            {
                "server": "100.73.162.127",
                "key": "QJztruGKKjvEcX9XBLMixf21wieLGYABEaWby97JP5s=",
                "device_id": "253474910",
            }
        ),
        encoding="utf-8",
    )

    info = m._read_channel_info(_settings(path))

    assert info == {
        "server": "100.73.162.127",
        "key": "QJztruGKKjvEcX9XBLMixf21wieLGYABEaWby97JP5s=",
        "device_id": "253474910",
    }


def test_missing_file_means_the_desktop_is_not_up_yet(tmp_path: Path) -> None:
    info = m._read_channel_info(_settings(tmp_path / "absent.json"))

    assert info == {"server": None, "key": None, "device_id": None}


def test_pending_device_id_stays_null_not_empty(tmp_path: Path) -> None:
    """Стол публикует адрес и ключ сразу, ID — когда его выдаст брокер."""
    path = tmp_path / "rustdesk.json"
    path.write_text(
        '{"server": "100.73.162.127", "key": "abc=", "device_id": null}',
        encoding="utf-8",
    )

    info = m._read_channel_info(_settings(path))

    assert info["server"] == "100.73.162.127"
    assert info["device_id"] is None


def test_corrupted_file_never_crashes_the_settings_surface(tmp_path: Path) -> None:
    path = tmp_path / "rustdesk.json"
    path.write_text("не json", encoding="utf-8")

    info = m._read_channel_info(_settings(path))

    assert info == {"server": None, "key": None, "device_id": None}


@pytest.mark.asyncio
async def test_endpoint_is_owner_gated_and_never_leaks_a_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "rustdesk.json"
    path.write_text(
        '{"server": "100.73.162.127", "key": "abc=", "device_id": "253474910"}',
        encoding="utf-8",
    )
    gate_calls: list[bool] = []

    async def fake_gate(_request, _engine, _settings):
        gate_calls.append(True)

    monkeypatch.setattr(m, "_resolve_owner_identity", fake_gate)
    response = SimpleNamespace(headers={})

    payload = await m.get_native_channel(
        SimpleNamespace(headers={}), response, object(), _settings(path)
    )

    assert gate_calls == [True]
    assert payload.available is True
    assert payload.device_id == "253474910"
    assert response.headers["Cache-Control"] == "no-store"
    # Пароля в контракте нет вовсе — нечему утекать.
    assert "password" not in payload.model_dump()


@pytest.mark.asyncio
async def test_channel_without_device_id_is_not_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "rustdesk.json"
    path.write_text('{"server": "100.73.162.127", "key": "abc="}', encoding="utf-8")

    async def fake_gate(_request, _engine, _settings):
        return None

    monkeypatch.setattr(m, "_resolve_owner_identity", fake_gate)

    payload = await m.get_native_channel(
        SimpleNamespace(headers={}), SimpleNamespace(headers={}), object(), _settings(path)
    )

    assert payload.available is False
    assert payload.server == "100.73.162.127"
