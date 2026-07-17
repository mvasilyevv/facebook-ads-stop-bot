from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from apps.api.routers.desktop_auth import (
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
