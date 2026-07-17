from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from apps.api.routers.desktop_auth import (
    DesktopReadyzCache,
    NetworkDesktopReadinessProbe,
    _decode_guacamole_instruction,
    _encode_guacamole_instruction,
)
from core.config import Settings


def _settings(port: int, password: str = "vnc-pass") -> Settings:
    return Settings(
        _env_file=None,
        require_api_key=False,
        desktop_guacd_host="127.0.0.1",
        desktop_guacd_port=port,
        desktop_vnc_password=SecretStr(password),
        desktop_readiness_timeout_seconds=1.0,
    )


@pytest.mark.asyncio
async def test_guacd_probe_requires_real_vnc_ready_handshake():
    observed: asyncio.Future[dict[str, str]] = asyncio.get_running_loop().create_future()

    async def guacd(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            assert _decode_guacamole_instruction(await reader.readuntil(b";")) == (
                "select",
                ["vnc"],
            )
            arguments = [
                "VERSION_1_5_0",
                "hostname",
                "port",
                "password",
                "width",
                "height",
                "disable-display-resize",
                "read-only",
            ]
            writer.write(_encode_guacamole_instruction("args", *arguments))
            await writer.drain()
            while True:
                opcode, values = _decode_guacamole_instruction(await reader.readuntil(b";"))
                if opcode == "connect":
                    observed.set_result(dict(zip(arguments, values, strict=True)))
                    break
            # `ready` is only emitted after the real plugin has initialized and
            # authenticated its upstream VNC connection.
            writer.write(_encode_guacamole_instruction("ready", "$test-connection"))
            await writer.drain()
            await reader.readuntil(b";")
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(guacd, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        result = await NetworkDesktopReadinessProbe()._guacd_vnc_ready(_settings(port))
        parameters = await asyncio.wait_for(observed, timeout=1)

    assert result is True
    assert parameters == {
        "VERSION_1_5_0": "VERSION_1_5_0",
        "hostname": "127.0.0.1",
        "port": "5900",
        "password": "vnc-pass",
        "width": "1366",
        "height": "768",
        "disable-display-resize": "true",
        "read-only": "false",
    }


@pytest.mark.asyncio
async def test_guacd_probe_fails_closed_on_vnc_error_and_missing_password():
    async def guacd_error(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b";")
            writer.write(
                _encode_guacamole_instruction(
                    "args", "VERSION_1_5_0", "hostname", "port", "password"
                )
            )
            await writer.drain()
            while _decode_guacamole_instruction(await reader.readuntil(b";"))[0] != "connect":
                pass
            writer.write(_encode_guacamole_instruction("error", "upstream failed", "519"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(guacd_error, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    probe = NetworkDesktopReadinessProbe()
    async with server:
        assert await probe._guacd_vnc_ready(_settings(port)) is False
    assert await probe._guacd_vnc_ready(_settings(port, password="")) is False


@pytest.mark.parametrize(
    "raw",
    [b"4.args,8.hostname", b"x.args;", b"4.args,9.short;", b"0.;"],
)
def test_guacamole_instruction_parser_rejects_malformed_data(raw: bytes):
    with pytest.raises(ValueError):
        _decode_guacamole_instruction(raw)


class _CountingProbe:
    """Фейковая проба: считает вызовы и отдаёт заранее заданные результаты."""

    def __init__(self, *results: dict[str, bool]) -> None:
        self.calls = 0
        self._results = list(results)
        self.release: asyncio.Event | None = None

    async def check(self, settings: Settings) -> dict[str, bool]:
        del settings
        self.calls += 1
        if self.release is not None:
            await self.release.wait()
        return self._results[min(self.calls, len(self._results)) - 1]


def _cache_settings(ttl: float) -> Settings:
    return Settings(
        _env_file=None,
        require_api_key=False,
        desktop_readiness_cache_seconds=ttl,
    )


# Повторный вызов в пределах TTL отдаёт кэш и не гоняет guacd→VNC-пробу заново —
# защита money-стека от истощения коннектов частыми запросами (ревью 17.07.2026).
@pytest.mark.asyncio
async def test_readyz_cache_serves_cached_result_within_ttl():
    clock = {"now": 100.0}
    probe = _CountingProbe({"guacamole": True, "jdbc": True, "guacd_vnc": True})
    cache = DesktopReadyzCache(monotonic=lambda: clock["now"])
    settings = _cache_settings(15.0)

    first = await cache.get(settings, probe)
    clock["now"] += 14.9
    second = await cache.get(settings, probe)

    assert probe.calls == 1
    assert first == second == {"guacamole": True, "jdbc": True, "guacd_vnc": True}


# Кэшируется и отрицательный результат: сломанный стек нельзя добивать пробами.
# После истечения TTL проба выполняется заново и отдаёт свежее состояние.
@pytest.mark.asyncio
async def test_readyz_cache_refreshes_after_ttl_including_not_ready():
    clock = {"now": 100.0}
    probe = _CountingProbe(
        {"guacamole": True, "jdbc": False, "guacd_vnc": True},
        {"guacamole": True, "jdbc": True, "guacd_vnc": True},
    )
    cache = DesktopReadyzCache(monotonic=lambda: clock["now"])
    settings = _cache_settings(15.0)

    stale = await cache.get(settings, probe)
    within_ttl = await cache.get(settings, probe)
    clock["now"] += 15.0
    refreshed = await cache.get(settings, probe)

    assert probe.calls == 2
    assert stale == within_ttl == {"guacamole": True, "jdbc": False, "guacd_vnc": True}
    assert refreshed == {"guacamole": True, "jdbc": True, "guacd_vnc": True}


# TTL <= 0 полностью отключает кэш — каждый вызов идёт в живую пробу (тестовый режим).
@pytest.mark.asyncio
async def test_readyz_cache_disabled_with_non_positive_ttl():
    probe = _CountingProbe({"guacamole": True, "jdbc": True, "guacd_vnc": True})
    cache = DesktopReadyzCache(monotonic=lambda: 100.0)

    await cache.get(_cache_settings(0), probe)
    await cache.get(_cache_settings(0), probe)

    assert probe.calls == 2


# Конкурентные запросы сериализуются локом: пробу выполняет ровно один,
# остальные ждут и получают её результат — нет параллельных VNC-handshake'ов.
@pytest.mark.asyncio
async def test_readyz_cache_runs_single_probe_for_concurrent_requests():
    probe = _CountingProbe({"guacamole": True, "jdbc": True, "guacd_vnc": True})
    probe.release = asyncio.Event()
    cache = DesktopReadyzCache(monotonic=lambda: 100.0)
    settings = _cache_settings(15.0)

    tasks = [asyncio.create_task(cache.get(settings, probe)) for _ in range(5)]
    await asyncio.sleep(0)
    probe.release.set()
    results = await asyncio.gather(*tasks)

    assert probe.calls == 1
    assert all(result == {"guacamole": True, "jdbc": True, "guacd_vnc": True} for result in results)
