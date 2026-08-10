# -*- coding: utf-8 -*-
"""Entrypoint MCP-сервера FB Agent (stdio transport).

Запускается Claude Desktop через config `claude_desktop_config.json` —
см. `docs/MCP_SETUP.md`. Также можно стартовать вручную для smoke-теста:

    .venv/bin/python run_mcp_server.py

КРИТИЧНО: stdio MCP-протокол использует stdout как бинарный канал
(JSON-RPC). Любая запись в stdout (включая print) сломает протокол.
Поэтому logging.basicConfig жёстко уходит в stderr.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from apps.mcp_server.main import main

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Claude Desktop посылает SIGINT при остановке — это нормальный shutdown.
        logging.getLogger(__name__).info("MCP-сервер остановлен по SIGINT")
