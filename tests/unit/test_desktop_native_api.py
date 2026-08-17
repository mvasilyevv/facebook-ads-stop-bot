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


def _launch_settings(path: Path, password: str = "s3cret-channel-pass") -> SimpleNamespace:
    from pydantic import SecretStr

    return SimpleNamespace(
        desktop_native_channel_path=path,
        desktop_rustdesk_password=SecretStr(password),
    )


def _published(tmp_path: Path, device_id: str = "253474910") -> Path:
    path = tmp_path / "rustdesk.json"
    path.write_text(
        json.dumps({"server": "desktop.example", "key": "abc=", "device_id": device_id}),
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_launch_link_carries_the_password_so_the_client_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Смысл ручки — вход без единого диалога.

    Схема `rustdesk://<id>?password=<pw>` разбирается клиентом и отправляется
    столу: проверено на живом канале — с неверным паролем клиент отвечает
    «Wrong password», а не пустым запросом. Без пароля в ссылке оператор
    каждый раз вводил бы его руками, ради чего кнопка и заводилась.
    """
    gate_calls: list[bool] = []

    async def fake_gate(_request, _engine, _settings):
        gate_calls.append(True)

    monkeypatch.setattr(m, "_resolve_owner_identity", fake_gate)
    response = SimpleNamespace(headers={})

    payload = await m.get_native_launch_link(
        SimpleNamespace(headers={}), response, object(), _launch_settings(_published(tmp_path))
    )

    assert gate_calls == [True]
    assert payload.url == "rustdesk://253474910?password=s3cret-channel-pass"
    # Ссылка с паролем не должна осесть ни в кэше, ни в реферере.
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_launch_link_percent_encodes_a_hostile_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пароль генерируем мы, но он может нести символы, значимые в URL.

    Сырые `&`, `#`, `/` и пробел обрезали бы query-строку и молча ушли бы в
    приложение неполным паролем — оператор увидел бы «Wrong password» без
    единой подсказки, что не так.
    """

    async def fake_gate(_request, _engine, _settings):
        return None

    monkeypatch.setattr(m, "_resolve_owner_identity", fake_gate)

    payload = await m.get_native_launch_link(
        SimpleNamespace(headers={}),
        SimpleNamespace(headers={}),
        object(),
        _launch_settings(_published(tmp_path), password="a&b#c/d e"),
    )

    assert payload.url == "rustdesk://253474910?password=a%26b%23c%2Fd%20e"


@pytest.mark.asyncio
async def test_launch_link_refuses_before_the_desktop_published_its_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ссылка с пустым ID открыла бы приложение в никуда."""
    from fastapi import HTTPException

    path = tmp_path / "rustdesk.json"
    path.write_text('{"server": "desktop.example", "key": "abc="}', encoding="utf-8")

    async def fake_gate(_request, _engine, _settings):
        return None

    monkeypatch.setattr(m, "_resolve_owner_identity", fake_gate)

    with pytest.raises(HTTPException) as error:
        await m.get_native_launch_link(
            SimpleNamespace(headers={}),
            SimpleNamespace(headers={}),
            object(),
            _launch_settings(path),
        )

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_launch_link_names_a_missing_password_instead_of_issuing_a_broken_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой пароль — дефект конфигурации сервера, а не «неверный пароль»."""
    from fastapi import HTTPException

    async def fake_gate(_request, _engine, _settings):
        return None

    monkeypatch.setattr(m, "_resolve_owner_identity", fake_gate)

    with pytest.raises(HTTPException) as error:
        await m.get_native_launch_link(
            SimpleNamespace(headers={}),
            SimpleNamespace(headers={}),
            object(),
            _launch_settings(_published(tmp_path), password=""),
        )

    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_launch_link_is_owner_gated_like_the_rest_of_the_desktop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ручка отдаёт пароль — гейт владельца обязателен, а не желателен."""
    from fastapi import HTTPException

    async def refusing_gate(_request, _engine, _settings):
        raise HTTPException(status_code=403, detail="Рабочий стол доступен только владельцу")

    monkeypatch.setattr(m, "_resolve_owner_identity", refusing_gate)

    with pytest.raises(HTTPException) as error:
        await m.get_native_launch_link(
            SimpleNamespace(headers={}),
            SimpleNamespace(headers={}),
            object(),
            _launch_settings(_published(tmp_path)),
        )

    assert error.value.status_code == 403


def test_channel_contract_still_has_no_password_field() -> None:
    """`/native` рендерится в разметку страницы — пароль туда не возвращается."""
    from apps.api.routers.v1.schemas.desktop import DesktopNativeChannelResponse

    assert "password" not in DesktopNativeChannelResponse.model_fields
