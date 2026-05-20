# -*- coding: utf-8 -*-
"""CLI Creator Recorder: start / stop / status через gRPC CreatorService."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from sqlalchemy import select

from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.config import get_settings
from core.crypto import decrypt
from core.db import get_session_factory
from core.models import VisionSettings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("creator_recorder")


async def _load_vision_settings() -> tuple[str, str, str]:
    """Загружает Vision-настройки из БД с fallback на .env."""
    settings = get_settings()
    factory = get_session_factory()
    try:
        async with factory() as session:
            row = await session.scalar(
                select(VisionSettings).where(VisionSettings.singleton_key == "default")
            )
            if row and row.x_token_encrypted and row.profile_id:
                token = decrypt(row.x_token_encrypted)
                if token:
                    return token, row.api_url or settings.vision_api_url, row.profile_id
    except Exception:
        logger.debug("Не удалось загрузить Vision-настройки из БД", exc_info=True)
    return settings.vision_x_token, settings.vision_api_url, settings.vision_profile_id


async def _make_client() -> BrowserAgentClient:
    """Создаёт и подключает gRPC клиент."""
    token, url, profile = await _load_vision_settings()
    if not token or not profile:
        raise SystemExit("Vision-настройки отсутствуют (token/profile_id)")
    settings = get_settings()
    config = BrowserAgentConfig(
        vision_x_token=token,
        vision_api_url=url,
        vision_profile_id=profile,
        vision_folder_id=getattr(settings, "vision_folder_id", None),
    )
    client = BrowserAgentClient(config)
    await client.start()
    await client.start_browser()
    return client


async def cmd_start(plan_name: str) -> int:
    client = await _make_client()
    try:
        started, message = await client.start_recording(plan_name)
        print(json.dumps({"started": started, "message": message}, ensure_ascii=False))
        return 0 if started else 1
    finally:
        await client.disconnect_browser()
        await client.close()


def _save_plan(output_path: Path, plan_json: str) -> None:
    """Синхронно сохраняет JSON плана на диск (вынесено во избежание блокирующего I/O в async)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(plan_json, encoding="utf-8")


async def cmd_stop(output_path: Path | None) -> int:
    client = await _make_client()
    try:
        stopped, plan_json, recorded = await client.stop_recording()
        if not stopped:
            print(json.dumps({"stopped": False}, ensure_ascii=False))
            return 1
        if output_path is not None:
            _save_plan(output_path, plan_json)
            print(f"План сохранён в {output_path} ({recorded} шагов)")
        else:
            print(plan_json)
        return 0
    finally:
        await client.disconnect_browser()
        await client.close()


async def cmd_status() -> int:
    client = await _make_client()
    try:
        recording, plan_name, recorded = await client.get_recorder_status()
        print(
            json.dumps(
                {"recording": recording, "plan_name": plan_name, "recorded_steps": recorded},
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        await client.disconnect_browser()
        await client.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Creator Recorder CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Начать запись плана")
    p_start.add_argument("plan_name", help="Имя плана для записи")

    p_stop = sub.add_parser("stop", help="Остановить запись и получить план")
    p_stop.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Куда сохранить JSON плана (если не задано — вывести в stdout)",
    )

    sub.add_parser("status", help="Показать статус recorder'а")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "start":
        return asyncio.run(cmd_start(args.plan_name))
    if args.command == "stop":
        return asyncio.run(cmd_stop(args.output))
    if args.command == "status":
        return asyncio.run(cmd_status())
    return 2


if __name__ == "__main__":
    sys.exit(main())
