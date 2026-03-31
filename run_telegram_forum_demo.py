# -*- coding: utf-8 -*-
"""Создаёт 4 forum topics в supergroup и отправляет тестовые сообщения без бизнес-логики."""

from __future__ import annotations

import argparse
import asyncio
import html
from dataclasses import dataclass
from datetime import UTC, datetime

from core.telegram.client import TelegramBotClient
from core.telegram.service import load_telegram_runtime_config

_TOPIC_COLORS = {
    "EARLY": 0x6FB9F0,
    "WARNING": 0xFFD67E,
    "STOP": 0xFF6F61,
    "ENABLE": 0x8EEE98,
}


@dataclass(slots=True, frozen=True)
class ForumDemoTopic:
    """Описание тестового forum topic."""

    stream: str
    title: str
    text: str


def _build_demo_topics(*, stamp: str) -> list[ForumDemoTopic]:
    """Собирает 4 тестовых потока для forum demo."""
    suffix = html.escape(stamp)
    return [
        ForumDemoTopic(
            stream="EARLY",
            title=f"DEMO EARLY {suffix}",
            text=(
                "🧪 <b>ТЕСТОВЫЙ EARLY-ПОТОК</b>\n"
                "Это изолированный forum topic без бизнес-логики.\n"
                "Здесь будут жить только ранние сигналы."
            ),
        ),
        ForumDemoTopic(
            stream="WARNING",
            title=f"DEMO WARNING {suffix}",
            text=(
                "🧪 <b>ТЕСТОВЫЙ WARNING-ПОТОК</b>\n"
                "Это изолированный forum topic без бизнес-логики.\n"
                "Здесь будут жить только предупреждения."
            ),
        ),
        ForumDemoTopic(
            stream="STOP",
            title=f"DEMO STOP {suffix}",
            text=(
                "🧪 <b>ТЕСТОВЫЙ STOP-ПОТОК</b>\n"
                "Это изолированный forum topic без бизнес-логики.\n"
                "Здесь будут жить стопы и lifecycle отключения."
            ),
        ),
        ForumDemoTopic(
            stream="ENABLE",
            title=f"DEMO ENABLE {suffix}",
            text=(
                "🧪 <b>ТЕСТОВЫЙ ENABLE-ПОТОК</b>\n"
                "Это изолированный forum topic без бизнес-логики.\n"
                "Здесь будут жить рекомендации на включение."
            ),
        ),
    ]


async def _load_runtime_token() -> str:
    """Загружает Telegram bot token из runtime-конфига."""
    token, _ = await load_telegram_runtime_config()
    if not token:
        raise RuntimeError("Не найден активный Telegram bot token.")
    return token


async def _ensure_forum_supergroup(client: TelegramBotClient, *, chat_id: str) -> dict:
    """Проверяет, что целевой чат является forum supergroup."""
    chat = await client.get_chat(chat_id=chat_id)
    chat_type = str(chat.get("type") or "")
    is_forum = bool(chat.get("is_forum"))
    if chat_type != "supergroup":
        raise RuntimeError(
            f"Чат {chat_id} не подходит: ожидается supergroup, сейчас type={chat_type!r}."
        )
    if not is_forum:
        raise RuntimeError(
            f"Чат {chat_id} не подходит: у supergroup не включён режим forum topics."
        )
    return chat


async def run_forum_demo(*, chat_id: str) -> None:
    """Создаёт test topics и отправляет в них demo-сообщения."""
    token = await _load_runtime_token()
    client = TelegramBotClient(token)
    try:
        chat = await _ensure_forum_supergroup(client, chat_id=chat_id)
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        print(f"forum_chat_id={chat_id}")
        print(f"forum_title={chat.get('title', '')}")

        for topic in _build_demo_topics(stamp=stamp):
            created_topic = await client.create_forum_topic(
                chat_id=chat_id,
                name=topic.title,
                icon_color=_TOPIC_COLORS.get(topic.stream),
            )
            message_thread_id = int(created_topic["message_thread_id"])
            sent_message = await client.send_message(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text=topic.text,
            )
            print(
                f"{topic.stream}: thread_id={message_thread_id} "
                f"message_id={sent_message.get('message_id')} title={topic.title}"
            )
    finally:
        await client.close()


def _parse_args() -> argparse.Namespace:
    """Парсит аргументы CLI."""
    parser = argparse.ArgumentParser(
        description="Создать 4 тестовых forum topics и отправить туда demo-сообщения."
    )
    parser.add_argument(
        "--chat-id",
        required=True,
        help="ID forum supergroup, где бот уже является администратором с правом manage_topics.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = _parse_args()
    asyncio.run(run_forum_demo(chat_id=args.chat_id))


if __name__ == "__main__":
    main()
