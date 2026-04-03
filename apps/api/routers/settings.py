# -*- coding: utf-8 -*-
"""FastAPI роутер для настроек observer, Telegram и управления workers."""

import asyncio
import logging
import os
import secrets
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import (
    InviteCodeResponse,
    ObserverSettingsSchema,
    ScanningToggleSchema,
    TelegramForumCutoverResponseSchema,
    TelegramPrimaryRecipientSchema,
    TelegramSettingsResponseSchema,
    TelegramSetTokenRequest,
)
from core.config import get_settings
from core.crypto import decrypt, encrypt
from core.domain import TelegramDeliveryMode, TelegramUserRole
from core.models import (
    TelegramInvite,
    TelegramSettings,
)
from core.observer.thresholds import (
    apply_observer_threshold_values,
    derive_legacy_stop_percent_of_base,
    derive_legacy_warning_percent_of_stop,
    extract_observer_threshold_values,
)
from core.settings_queries import (
    get_observer_settings as _get_settings,
)
from core.settings_queries import (
    get_or_create_observer_settings as _get_or_create_settings,
)
from core.telegram.client import TelegramBotClient
from core.telegram.service import (
    CONTROL_TOPIC_NAME,
    FORUM_STREAM_TOPIC_NAMES,
    FORUM_SUPERGROUP_CHAT_ID,
    build_telegram_deep_link,
    forum_cutover_status_from_settings,
    forum_topics_ready,
    get_latest_active_invite,
    get_or_create_telegram_settings,
    mask_chat_id,
    poller_status_from_settings,
    revoke_telegram_access_records,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings/observer", response_model=ObserverSettingsSchema)
async def get_observer_settings(db: AsyncSession = Depends(get_db)):
    """Получить настройки observer."""
    row = await _get_settings(db)
    threshold_values = extract_observer_threshold_values(row)
    if row is None:
        return ObserverSettingsSchema(**threshold_values)
    return ObserverSettingsSchema(
        interval_seconds=row.interval_seconds,
        jitter_seconds=row.jitter_seconds,
        **threshold_values,
        is_scanning_enabled=row.is_scanning_enabled,
    )


@router.put("/settings/observer", response_model=ObserverSettingsSchema)
async def update_observer_settings(
    body: ObserverSettingsSchema, db: AsyncSession = Depends(get_db)
):
    """Обновить настройки observer (upsert singleton)."""
    row = await _get_or_create_settings(db)
    threshold_values = extract_observer_threshold_values(body)
    if "warning_percent_of_stop" not in body.model_fields_set:
        threshold_values["warning_percent_of_stop"] = derive_legacy_warning_percent_of_stop(
            threshold_values
        )
    if "stop_percent_of_base" not in body.model_fields_set:
        threshold_values["stop_percent_of_base"] = derive_legacy_stop_percent_of_base(
            threshold_values
        )
    row.interval_seconds = body.interval_seconds
    row.jitter_seconds = body.jitter_seconds
    apply_observer_threshold_values(row, threshold_values)
    row.is_scanning_enabled = body.is_scanning_enabled
    await db.commit()
    return ObserverSettingsSchema(
        interval_seconds=row.interval_seconds,
        jitter_seconds=row.jitter_seconds,
        **extract_observer_threshold_values(row),
        is_scanning_enabled=row.is_scanning_enabled,
    )


@router.patch("/settings/observer/scanning")
async def toggle_scanning(body: ScanningToggleSchema, db: AsyncSession = Depends(get_db)):
    """Быстрое переключение сканирования без изменения остальных настроек."""
    row = await _get_or_create_settings(db)
    row.is_scanning_enabled = body.enabled
    await db.commit()
    return {"is_scanning_enabled": row.is_scanning_enabled}


@router.post("/settings/observer/scan-now")
async def trigger_scan_now(db: AsyncSession = Depends(get_db)):
    """Установить флаг немедленного скана — воркер выполнит скан при следующей проверке."""
    row = await _get_or_create_settings(db)
    row.scan_requested = True
    await db.commit()
    return {"scan_requested": True}


def _observer_runtime_paths() -> tuple[Path, Path, Path, str]:
    """Возвращает пути и python-бинарь для управления observer worker."""
    project_root = Path(__file__).parent.parent.parent
    pid_file = project_root / ".logs" / "pids.txt"
    log_file = project_root / ".logs" / "observer.log"
    run_script = project_root / "run_observer.py"
    venv_python = project_root / ".venv" / "bin" / "python"
    python_bin = str(venv_python) if venv_python.exists() else sys.executable
    return pid_file, log_file, run_script, python_bin


def _disable_runtime_paths() -> tuple[Path, Path, Path, str]:
    """Возвращает пути и python-бинарь для управления воркером отключения."""
    project_root = Path(__file__).parent.parent.parent
    pid_file = project_root / ".logs" / "pids.txt"
    log_file = project_root / ".logs" / "disable_worker.log"
    run_script = project_root / "run_disable_worker.py"
    venv_python = project_root / ".venv" / "bin" / "python"
    python_bin = str(venv_python) if venv_python.exists() else sys.executable
    return pid_file, log_file, run_script, python_bin


def _read_lines_from_file(path: Path) -> list[str]:
    """Читает файл построчно или возвращает пустой список, если файла нет."""
    if not path.exists():
        return []
    return path.read_text().splitlines()


def _write_pid_lines(path: Path, lines: list[str]) -> None:
    """Перезаписывает PID-файл подготовленным списком строк."""
    path.write_text("\n".join(lines) + "\n" if lines else "")


def _read_pid_from_file(path: Path) -> int | None:
    """Читает PID из файла-одиночки."""
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None


def _unlink_file(path: Path) -> None:
    """Удаляет файл, если он существует."""
    path.unlink(missing_ok=True)


def _append_text(path: Path, text: str) -> None:
    """Дописвает строку в конец файла."""
    with path.open("a") as handle:
        handle.write(text)


def _open_append_handle(path: Path):
    """Открывает файловый дескриптор в режиме append."""
    return path.open("a")


async def _stop_observer_process() -> int | None:
    """Останавливает текущий observer worker и удаляет его PID из файла."""
    pid_file, _, _, _ = _observer_runtime_paths()

    # Находим и завершаем текущий процесс воркера
    old_pid: int | None = None
    lines = await asyncio.to_thread(_read_lines_from_file, pid_file)
    if lines:
        remaining = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) == 2 and parts[1] == "observer":
                try:
                    old_pid = int(parts[0])
                except ValueError:
                    pass
            else:
                remaining.append(line)

        if old_pid:
            try:
                os.kill(old_pid, signal.SIGTERM)
                # Даём время на graceful shutdown
                await asyncio.sleep(2.0)
                # Проверяем что процесс завершился
                try:
                    os.kill(old_pid, 0)
                    # Всё ещё жив — SIGKILL
                    os.kill(old_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass  # Процесс уже не существует
            await asyncio.to_thread(_write_pid_lines, pid_file, remaining)
    return old_pid


async def _stop_disable_process() -> int | None:
    """Останавливает текущий воркер отключения и удаляет его PID из файла."""
    pid_file, _, _, _ = _disable_runtime_paths()
    singleton_pid_file = Path("/tmp/fb_disable_worker.pid")

    old_pid = await asyncio.to_thread(_read_pid_from_file, singleton_pid_file)

    lines = await asyncio.to_thread(_read_lines_from_file, pid_file)
    if lines:
        remaining = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) == 2 and parts[1] == "disable_worker":
                if old_pid is None:
                    try:
                        old_pid = int(parts[0])
                    except ValueError:
                        pass
            else:
                remaining.append(line)
        await asyncio.to_thread(_write_pid_lines, pid_file, remaining)

    if old_pid:
        try:
            os.kill(old_pid, signal.SIGTERM)
            await asyncio.sleep(2.0)
            try:
                os.kill(old_pid, 0)
                os.kill(old_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass

    await asyncio.to_thread(_unlink_file, singleton_pid_file)
    return old_pid


async def _start_observer_process(*, reason: str) -> int:
    """Запускает observer worker и сохраняет его PID в файл."""
    pid_file, log_file, run_script, python_bin = _observer_runtime_paths()

    await asyncio.to_thread(
        _append_text,
        log_file,
        f"\n--- {reason} {datetime.now(UTC).isoformat()} ---\n",
    )
    stdout_handle = await asyncio.to_thread(_open_append_handle, log_file)

    try:
        proc = await asyncio.create_subprocess_exec(
            python_bin,
            str(run_script),
            stdout=stdout_handle,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(run_script.parent),
        )
    finally:
        stdout_handle.close()

    # Сохраняем новый PID
    await asyncio.to_thread(_append_text, pid_file, f"{proc.pid} observer\n")

    return proc.pid


async def _start_disable_process(*, reason: str) -> int:
    """Запускает воркер отключения и сохраняет его PID в файл."""
    pid_file, log_file, run_script, python_bin = _disable_runtime_paths()

    await asyncio.to_thread(
        _append_text,
        log_file,
        f"\n--- {reason} {datetime.now(UTC).isoformat()} ---\n",
    )
    stdout_handle = await asyncio.to_thread(_open_append_handle, log_file)

    try:
        proc = await asyncio.create_subprocess_exec(
            python_bin,
            str(run_script),
            stdout=stdout_handle,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(run_script.parent),
        )
    finally:
        stdout_handle.close()

    await asyncio.to_thread(_append_text, pid_file, f"{proc.pid} disable_worker\n")

    return proc.pid


@router.post("/observer/restart")
async def restart_observer():
    """Перезапуск observer worker: завершает текущий процесс и запускает новый."""
    old_pid = await _stop_observer_process()
    new_pid = await _start_observer_process(reason="Перезапуск воркера через UI")

    return {"restarted": True, "old_pid": old_pid, "new_pid": new_pid}


@router.post("/disable-worker/restart")
async def restart_disable_worker():
    """Перезапуск воркера отключения: завершает зависший процесс и поднимает новый."""
    old_pid = await _stop_disable_process()
    new_pid = await _start_disable_process(reason="Перезапуск воркера отключения через интерфейс")

    return {"restarted": True, "old_pid": old_pid, "new_pid": new_pid}


def _mask_bot_token(token: str) -> str:
    """Маскирует bot token для безопасного отображения."""
    return (token[:10] + "***") if len(token) > 10 else ("***" if token else "")


def _serialize_primary_recipient(
    row: TelegramSettings | None,
) -> TelegramPrimaryRecipientSchema | None:
    """Собирает primary recipient из telegram_settings."""
    if row is None or not row.chat_id:
        return None
    return TelegramPrimaryRecipientSchema(
        chat_id=row.chat_id,
        masked_chat_id=mask_chat_id(row.chat_id),
        telegram_user_id=row.owner_telegram_user_id or "",
        username=row.owner_username or "",
        first_name=row.owner_first_name or "",
        role=TelegramUserRole.OWNER.value,
    )


def _activation_command(code: str) -> str:
    """Строит текст команды активации для Telegram."""
    return f"/start {code}".strip() if code else ""


def _serialize_invite_response(
    invite: TelegramInvite | None,
    *,
    bot_username: str,
    delivery_mode: str = TelegramDeliveryMode.PRIVATE_CHAT.value,
) -> InviteCodeResponse | None:
    """Сериализует активный инвайт для UI."""
    if invite is None:
        return None
    activation_command = _activation_command(invite.code)
    return InviteCodeResponse(
        code=invite.code,
        bot_username=bot_username or "",
        role=invite.role or TelegramUserRole.RECIPIENT.value,
        expires_at=invite.expires_at.isoformat() if invite.expires_at else None,
        deep_link=(
            ""
            if str(delivery_mode).upper() == TelegramDeliveryMode.FORUM_GROUP.value
            else build_telegram_deep_link(bot_username or "", invite.code)
        ),
        activation_command=activation_command,
        activation_target=CONTROL_TOPIC_NAME,
    )


async def _create_forum_topics_if_needed(
    *,
    bot_token: str,
    settings_row: TelegramSettings,
) -> dict[str, int]:
    """Создаёт production topics для forum supergroup или переиспользует уже сохранённые."""
    if settings_row.chat_id == FORUM_SUPERGROUP_CHAT_ID and forum_topics_ready(settings_row):
        return {
            "control_topic_id": int(settings_row.control_topic_id or 0),
            "early_topic_id": int(settings_row.early_topic_id or 0),
            "warning_topic_id": int(settings_row.warning_topic_id or 0),
            "stop_topic_id": int(settings_row.stop_topic_id or 0),
            "enable_topic_id": int(settings_row.enable_topic_id or 0),
        }

    client = TelegramBotClient(bot_token)
    try:
        chat = await client.get_chat(chat_id=FORUM_SUPERGROUP_CHAT_ID)
        if str(chat.get("type") or "") != "supergroup":
            raise HTTPException(
                status_code=400,
                detail="Telegram-группа для cutover должна быть supergroup.",
            )
        if not bool(chat.get("is_forum")):
            raise HTTPException(
                status_code=400,
                detail="В Telegram-группе не включён режим forum topics.",
            )

        created_topics: dict[str, int] = {}
        for stream_key, title in FORUM_STREAM_TOPIC_NAMES.items():
            topic = await client.create_forum_topic(
                chat_id=FORUM_SUPERGROUP_CHAT_ID,
                name=title,
            )
            topic_id = int(topic["message_thread_id"])
            if stream_key == "CONTROL":
                created_topics["control_topic_id"] = topic_id
            elif stream_key == "EARLY":
                created_topics["early_topic_id"] = topic_id
            elif stream_key == "WARNING":
                created_topics["warning_topic_id"] = topic_id
            elif stream_key == "STOP":
                created_topics["stop_topic_id"] = topic_id
            elif stream_key == "ENABLE":
                created_topics["enable_topic_id"] = topic_id
        return created_topics
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Не удалось подготовить forum topics. Проверьте права бота в группе.",
        ) from exc
    finally:
        await client.close()


async def _prepare_telegram_forum_cutover(
    *,
    db: AsyncSession,
    settings_row: TelegramSettings,
    bot_token: str,
    bot_username: str,
) -> TelegramForumCutoverResponseSchema:
    """Готовит Telegram-контур к переезду в forum supergroup."""
    topic_ids = await _create_forum_topics_if_needed(
        bot_token=bot_token,
        settings_row=settings_row,
    )
    auth_code = str(secrets.randbelow(900000) + 100000)
    await revoke_telegram_access_records(db)

    settings_row.delivery_mode = TelegramDeliveryMode.FORUM_GROUP
    settings_row.chat_id = FORUM_SUPERGROUP_CHAT_ID
    settings_row.is_authorized = False
    settings_row.auth_code = auth_code
    settings_row.owner_telegram_user_id = ""
    settings_row.owner_username = ""
    settings_row.owner_first_name = ""
    settings_row.bot_username = bot_username or settings_row.bot_username or ""
    settings_row.control_topic_id = topic_ids["control_topic_id"]
    settings_row.early_topic_id = topic_ids["early_topic_id"]
    settings_row.warning_topic_id = topic_ids["warning_topic_id"]
    settings_row.stop_topic_id = topic_ids["stop_topic_id"]
    settings_row.enable_topic_id = topic_ids["enable_topic_id"]
    await db.commit()

    return TelegramForumCutoverResponseSchema(
        bot_username=settings_row.bot_username or "",
        chat_id=FORUM_SUPERGROUP_CHAT_ID,
        auth_code=auth_code,
        activation_command=_activation_command(auth_code),
        control_topic_id=settings_row.control_topic_id,
        early_topic_id=settings_row.early_topic_id,
        warning_topic_id=settings_row.warning_topic_id,
        stop_topic_id=settings_row.stop_topic_id,
        enable_topic_id=settings_row.enable_topic_id,
        forum_cutover_status=forum_cutover_status_from_settings(settings_row),
        message=(
            "Forum topics готовы. Откройте topic CONTROL в группе AdGuard FB Bot "
            "и отправьте команду активации."
        ),
    )


@router.get("/settings/telegram", response_model=TelegramSettingsResponseSchema)
async def get_telegram_settings(db: AsyncSession = Depends(get_db)):
    """Получить настройки Telegram (токен маскируется)."""
    row = await db.scalar(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    if row is None:
        s = get_settings()
        primary_recipient = None
        if s.telegram_chat_id:
            primary_recipient = TelegramPrimaryRecipientSchema(
                chat_id=s.telegram_chat_id,
                masked_chat_id=mask_chat_id(s.telegram_chat_id),
                role=TelegramUserRole.OWNER.value,
            )
        return TelegramSettingsResponseSchema(
            bot_token=_mask_bot_token(s.telegram_bot_token),
            chat_id=s.telegram_chat_id,
            forum_chat_id="",
            is_authorized=bool(s.telegram_chat_id),
            delivery_mode=TelegramDeliveryMode.PRIVATE_CHAT.value,
            poller_status="OFFLINE",
            forum_cutover_status="NOT_CONFIGURED",
            activation_command="",
            primary_recipient=primary_recipient,
        )

    token = decrypt(row.bot_token_encrypted) if row.bot_token_encrypted else ""
    active_invite = await get_latest_active_invite(db)
    delivery_mode = str(getattr(row, "delivery_mode", TelegramDeliveryMode.PRIVATE_CHAT.value))
    auth_code = row.auth_code if not row.is_authorized else ""
    auth_deep_link = ""
    if delivery_mode != TelegramDeliveryMode.FORUM_GROUP.value:
        auth_deep_link = build_telegram_deep_link(row.bot_username or "", auth_code or "")
    return TelegramSettingsResponseSchema(
        bot_token=_mask_bot_token(token),
        chat_id=row.chat_id,
        forum_chat_id=row.chat_id
        if delivery_mode == TelegramDeliveryMode.FORUM_GROUP.value
        else "",
        is_authorized=row.is_authorized,
        bot_username=row.bot_username,
        auth_code=auth_code,
        delivery_mode=delivery_mode,
        control_topic_id=row.control_topic_id,
        early_topic_id=row.early_topic_id,
        warning_topic_id=row.warning_topic_id,
        stop_topic_id=row.stop_topic_id,
        enable_topic_id=row.enable_topic_id,
        poller_status=poller_status_from_settings(row),
        last_poller_heartbeat_at=(
            row.poller_heartbeat_at.isoformat() if row.poller_heartbeat_at else None
        ),
        auth_deep_link=auth_deep_link,
        activation_command=_activation_command(auth_code),
        forum_cutover_status=forum_cutover_status_from_settings(row),
        primary_recipient=_serialize_primary_recipient(row),
        active_invite=_serialize_invite_response(
            active_invite,
            bot_username=row.bot_username or "",
            delivery_mode=delivery_mode,
        ),
    )


@router.put("/settings/telegram/token")
async def set_telegram_token(body: TelegramSetTokenRequest, db: AsyncSession = Depends(get_db)):
    """Установить bot_token и сразу подготовить forum-cutover."""
    import httpx

    token = body.bot_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Токен не может быть пустым")

    # Проверяем токен через Telegram API getMe
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
            if not data.get("ok"):
                raise HTTPException(status_code=400, detail="Невалидный токен бота")
            bot_info = data["result"]
            bot_username = bot_info.get("username", "")
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=400, detail="Не удалось подключиться к Telegram API"
        ) from exc

    row = await get_or_create_telegram_settings(db)
    row.bot_token_encrypted = encrypt(token)
    row.bot_username = bot_username
    await db.flush()

    cutover = await _prepare_telegram_forum_cutover(
        db=db,
        settings_row=row,
        bot_token=token,
        bot_username=bot_username,
    )
    return {
        "bot_username": cutover.bot_username,
        "auth_code": cutover.auth_code,
        "auth_deep_link": "",
        "activation_command": cutover.activation_command,
        "control_topic_id": cutover.control_topic_id,
        "forum_cutover_status": cutover.forum_cutover_status,
        "message": cutover.message,
    }


@router.post(
    "/api/settings/telegram/forum/cutover",
    response_model=TelegramForumCutoverResponseSchema,
)
async def prepare_telegram_forum_cutover(db: AsyncSession = Depends(get_db)):
    """Готовит Telegram-контур к переезду в forum supergroup без смены токена."""
    row = await db.scalar(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    if row is None or not row.bot_token_encrypted:
        raise HTTPException(status_code=400, detail="Telegram-бот не настроен")

    token = decrypt(row.bot_token_encrypted)
    if not token:
        raise HTTPException(status_code=400, detail="Не удалось прочитать токен Telegram-бота")

    return await _prepare_telegram_forum_cutover(
        db=db,
        settings_row=row,
        bot_token=token,
        bot_username=row.bot_username or "",
    )


@router.delete("/settings/telegram")
async def revoke_telegram(db: AsyncSession = Depends(get_db)):
    """Отозвать авторизацию Telegram — сбрасывает все настройки."""
    row = await db.scalar(
        select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
    )
    if row is not None:
        await revoke_telegram_access_records(db)
        row.bot_token_encrypted = ""
        row.chat_id = ""
        row.is_authorized = False
        row.auth_code = ""
        row.bot_username = ""
        row.owner_telegram_user_id = ""
        row.owner_username = ""
        row.owner_first_name = ""
        row.delivery_mode = TelegramDeliveryMode.PRIVATE_CHAT
        row.control_topic_id = None
        row.early_topic_id = None
        row.warning_topic_id = None
        row.stop_topic_id = None
        row.enable_topic_id = None
        await db.commit()
    return {"status": "ok"}
