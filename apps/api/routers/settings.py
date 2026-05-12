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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import (
    AutoEnableToggleSchema,
    InviteCodeResponse,
    ObserverSettingsSchema,
    ScanningToggleSchema,
    TelegramPrimaryRecipientSchema,
    TelegramSettingsResponseSchema,
    TelegramSetTokenRequest,
)
from core.config import get_settings
from core.crypto import decrypt, encrypt
from core.domain import TelegramUserRole
from core.models import (
    TelegramInvite,
    TelegramSettings,
    VisionSettings,
)
from core.settings_queries import (
    get_observer_settings as _get_settings,
)
from core.settings_queries import (
    get_or_create_observer_settings as _get_or_create_settings,
)
from core.telegram.service import (
    build_telegram_deep_link,
    get_latest_active_invite,
    get_or_create_telegram_settings,
    mask_chat_id,
    poller_status_from_settings,
    revoke_telegram_access_records,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["settings"])


async def _get_or_start_browser_agent_session_id(
    browser_stub, db: AsyncSession, *, start_if_missing: bool
) -> str:
    """Вернуть активную browser-agent сессию и стартовать её только для явной команды."""
    import grpc

    from clients.python_grpc.v1 import browser_session_pb2

    try:
        session_resp = await browser_stub.GetSessionInfo(
            browser_session_pb2.GetSessionInfoRequest(session_id="")
        )
        if session_resp.session_id:
            return session_resp.session_id
    except grpc.RpcError as exc:
        if exc.code() != grpc.StatusCode.NOT_FOUND:
            raise

    if not start_if_missing:
        raise HTTPException(
            status_code=409,
            detail="Активная browser-agent сессия не найдена",
        )

    row = await db.scalar(select(VisionSettings).where(VisionSettings.singleton_key == "default"))
    settings = get_settings()
    x_token = decrypt(row.x_token_encrypted or "") if row and row.x_token_encrypted else ""
    api_url = (row.api_url if row else "") or settings.vision_api_url
    profile_id = (row.profile_id if row else "") or settings.vision_profile_id

    if not x_token:
        x_token = settings.vision_x_token

    if not x_token or not profile_id:
        raise HTTPException(
            status_code=400,
            detail="Vision X-Token или профиль не настроены",
        )

    start_resp = await browser_stub.StartBrowser(
        browser_session_pb2.StartBrowserRequest(
            vision_x_token=x_token,
            vision_api_url=api_url,
            vision_profile_id=profile_id,
            viewport_width=1280,
            viewport_height=800,
        )
    )
    return start_resp.session_id


@router.get("/settings/observer", response_model=ObserverSettingsSchema)
async def get_observer_settings(db: AsyncSession = Depends(get_db)):
    """Получить настройки observer."""
    row = await _get_settings(db)
    if row is None:
        return ObserverSettingsSchema()
    return ObserverSettingsSchema(
        is_scanning_enabled=row.is_scanning_enabled,
        auto_enable_recommendations=bool(row.auto_enable_recommendations),
        pause_until=row.pause_until,
    )


@router.put("/settings/observer", response_model=ObserverSettingsSchema)
async def update_observer_settings(
    body: ObserverSettingsSchema, db: AsyncSession = Depends(get_db)
):
    """Обновить настройки observer (upsert singleton)."""
    row = await _get_or_create_settings(db)
    row.is_scanning_enabled = body.is_scanning_enabled
    if "auto_enable_recommendations" in body.model_fields_set:
        row.auto_enable_recommendations = body.auto_enable_recommendations
    await db.commit()
    return ObserverSettingsSchema(
        is_scanning_enabled=row.is_scanning_enabled,
        auto_enable_recommendations=bool(row.auto_enable_recommendations),
        pause_until=row.pause_until,
    )


@router.patch("/settings/observer/scanning")
async def toggle_scanning(body: ScanningToggleSchema, db: AsyncSession = Depends(get_db)):
    """Быстрое переключение сканирования без изменения остальных настроек."""
    row = await _get_or_create_settings(db)
    row.is_scanning_enabled = body.enabled
    if body.enabled:
        # При включении сканирования снимаем паузу
        row.pause_until = None
    await db.commit()
    return {"is_scanning_enabled": row.is_scanning_enabled, "pause_until": row.pause_until}


@router.post("/settings/observer/scan-now")
async def trigger_scan_now(db: AsyncSession = Depends(get_db)):
    """Установить флаг немедленного скана — воркер выполнит скан при следующей проверке."""
    row = await _get_or_create_settings(db)
    row.scan_requested = True
    await db.commit()
    return {"scan_requested": True}


@router.patch("/settings/observer/auto-enable")
async def toggle_auto_enable(body: AutoEnableToggleSchema, db: AsyncSession = Depends(get_db)):
    """Быстрое переключение авто-включения объявлений по рекомендациям."""
    row = await _get_or_create_settings(db)
    row.auto_enable_recommendations = body.enabled
    await db.commit()
    return {"auto_enable_recommendations": row.auto_enable_recommendations}


def _observer_runtime_paths() -> tuple[Path, Path, Path, str]:
    """Возвращает пути и python-бинарь для управления observer worker."""
    project_root = Path(__file__).resolve().parents[3]
    pid_file = project_root / ".logs" / "pids.txt"
    log_file = project_root / ".logs" / "observer.log"
    run_script = project_root / "run_observer.py"
    venv_python = project_root / ".venv" / "bin" / "python"
    python_bin = str(venv_python) if venv_python.exists() else sys.executable
    return pid_file, log_file, run_script, python_bin


def _disable_runtime_paths() -> tuple[Path, Path, Path, str]:
    """Возвращает пути и python-бинарь для управления воркером отключения."""
    project_root = Path(__file__).resolve().parents[3]
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


@router.get("/browser/validate-columns", include_in_schema=False)
@router.get("/settings/browser/validate-columns")
async def validate_browser_columns(
    start_if_missing: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Проверить наличие всех необходимых колонок в таблице Ads Manager через gRPC."""
    import grpc

    from clients.python_grpc.v1 import (
        browser_session_pb2_grpc,
        scanner_pb2,
        scanner_pb2_grpc,
    )

    channel = grpc.aio.insecure_channel("localhost:50051")
    try:
        browser_stub = browser_session_pb2_grpc.BrowserSessionServiceStub(channel)
        scanner_stub = scanner_pb2_grpc.ScannerServiceStub(channel)
        session_id = await _get_or_start_browser_agent_session_id(
            browser_stub, db, start_if_missing=start_if_missing
        )

        result = await scanner_stub.ValidateColumns(
            scanner_pb2.ValidateColumnsRequest(session_id=session_id)
        )
        return {
            "valid": result.valid,
            "missing_columns": list(result.missing_columns),
            "found_columns": list(result.found_columns),
            "error_message": result.error_message,
        }
    except grpc.RpcError as e:
        raise HTTPException(status_code=502, detail=f"gRPC ошибка: {e.details()}") from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        await channel.close()


def _normalize_saved_column_widths(raw_value: object) -> list[dict[str, object]]:
    """Нормализовать сохранённый JSON слепка ширины колонок Ads Manager."""
    if not isinstance(raw_value, list):
        return []

    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in raw_value:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        title = str(item.get("title") or "").strip()
        surface_key = str(item.get("surface_key") or item.get("surfaceKey") or "").strip()
        try:
            width_px = int(float(item.get("width_px") or item.get("widthPx") or 0))
        except (TypeError, ValueError):
            width_px = 0
        text_needles_raw = item.get("text_needles") or item.get("textNeedles") or []
        text_needles = (
            [str(needle).strip() for needle in text_needles_raw if str(needle).strip()]
            if isinstance(text_needles_raw, list)
            else []
        )
        if not key or not surface_key or width_px <= 0 or key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "key": key,
                "title": title,
                "surface_key": surface_key,
                "width_px": width_px,
                "text_needles": text_needles,
            }
        )
    return result


@router.post("/browser/save-column-widths", include_in_schema=False)
@router.post("/settings/browser/save-column-widths")
async def save_browser_column_widths(db: AsyncSession = Depends(get_db)):
    """Сохранить текущий слепок ширины колонок Ads Manager через gRPC."""
    import grpc

    from clients.python_grpc.v1 import (
        browser_session_pb2_grpc,
        scanner_pb2,
        scanner_pb2_grpc,
    )

    channel = grpc.aio.insecure_channel("localhost:50051")
    try:
        browser_stub = browser_session_pb2_grpc.BrowserSessionServiceStub(channel)
        scanner_stub = scanner_pb2_grpc.ScannerServiceStub(channel)
        session_id = await _get_or_start_browser_agent_session_id(
            browser_stub, db, start_if_missing=True
        )

        result = await scanner_stub.CaptureColumnWidths(
            scanner_pb2.CaptureColumnWidthsRequest(session_id=session_id)
        )
        column_widths = [
            {
                "key": column.key,
                "title": column.title,
                "surface_key": column.surface_key,
                "width_px": int(column.width_px),
                "text_needles": list(column.text_needles),
            }
            for column in result.column_widths
            if column.key and column.surface_key and int(column.width_px) > 0
        ]

        if not result.captured or not column_widths:
            return {
                "saved": False,
                "saved_count": 0,
                "matched_columns": list(result.matched_columns),
                "error_message": result.error_message
                or "Не удалось сохранить слепок ширины колонок Ads Manager",
                "total_width_px": result.total_width_px,
            }

        row = await db.scalar(
            select(VisionSettings).where(VisionSettings.singleton_key == "default")
        )
        if row is None:
            row = VisionSettings(singleton_key="default")
            db.add(row)
        row.column_widths_json = column_widths
        await db.commit()

        return {
            "saved": True,
            "saved_count": len(column_widths),
            "matched_columns": [column["title"] for column in column_widths],
            "error_message": "",
            "total_width_px": result.total_width_px,
        }
    except grpc.RpcError as e:
        raise HTTPException(status_code=502, detail=f"gRPC ошибка: {e.details()}") from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось сохранить слепок ширины колонок: {e}",
        ) from e
    finally:
        await channel.close()


@router.post("/browser/apply-column-widths", include_in_schema=False)
@router.post("/settings/browser/apply-column-widths")
async def apply_browser_column_widths(db: AsyncSession = Depends(get_db)):
    """Применить сохранённую ручную ширину колонок Ads Manager через gRPC."""
    import grpc

    from clients.python_grpc.v1 import (
        browser_session_pb2_grpc,
        scanner_pb2,
        scanner_pb2_grpc,
    )

    channel = grpc.aio.insecure_channel("localhost:50051")
    try:
        browser_stub = browser_session_pb2_grpc.BrowserSessionServiceStub(channel)
        scanner_stub = scanner_pb2_grpc.ScannerServiceStub(channel)
        session_id = await _get_or_start_browser_agent_session_id(
            browser_stub, db, start_if_missing=True
        )
        row = await db.scalar(
            select(VisionSettings).where(VisionSettings.singleton_key == "default")
        )
        saved_widths = _normalize_saved_column_widths(
            row.column_widths_json if row is not None else []
        )

        result = await scanner_stub.ApplyColumnWidths(
            scanner_pb2.ApplyColumnWidthsRequest(
                session_id=session_id,
                column_widths=[
                    scanner_pb2.ColumnWidth(
                        key=str(column["key"]),
                        title=str(column["title"]),
                        surface_key=str(column["surface_key"]),
                        width_px=int(column["width_px"]),
                        text_needles=list(column["text_needles"]),
                    )
                    for column in saved_widths
                ],
            )
        )
        return {
            "applied": result.applied,
            "matched_columns": list(result.matched_columns),
            "missing_columns": list(result.missing_columns),
            "error_message": result.error_message,
            "adjusted_cells": result.adjusted_cells,
            "total_width_px": result.total_width_px,
            "used_saved_widths": bool(saved_widths),
        }
    except grpc.RpcError as e:
        raise HTTPException(status_code=502, detail=f"gRPC ошибка: {e.details()}") from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось применить ширины колонок: {e}",
        ) from e
    finally:
        await channel.close()


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
        deep_link=build_telegram_deep_link(bot_username or "", invite.code),
        activation_command=activation_command,
        activation_target="",
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
            is_authorized=bool(s.telegram_chat_id),
            poller_status="OFFLINE",
            activation_command="",
            primary_recipient=primary_recipient,
            web_app_url=(s.web_app_url or ""),
        )

    token = decrypt(row.bot_token_encrypted) if row.bot_token_encrypted else ""
    active_invite = await get_latest_active_invite(db)
    auth_code = row.auth_code if not row.is_authorized else ""
    auth_deep_link = build_telegram_deep_link(row.bot_username or "", auth_code or "")
    return TelegramSettingsResponseSchema(
        bot_token=_mask_bot_token(token),
        chat_id=row.chat_id,
        is_authorized=row.is_authorized,
        bot_username=row.bot_username,
        auth_code=auth_code,
        poller_status=poller_status_from_settings(row),
        last_poller_heartbeat_at=(
            row.poller_heartbeat_at.isoformat() if row.poller_heartbeat_at else None
        ),
        auth_deep_link=auth_deep_link,
        activation_command=_activation_command(auth_code),
        primary_recipient=_serialize_primary_recipient(row),
        active_invite=_serialize_invite_response(
            active_invite,
            bot_username=row.bot_username or "",
        ),
        web_app_url=(getattr(row, "web_app_url", None) or ""),
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
    row.auth_code = str(secrets.randbelow(900000) + 100000)
    await db.commit()
    return {
        "bot_username": bot_username,
        "auth_code": row.auth_code,
        "auth_deep_link": build_telegram_deep_link(bot_username, row.auth_code),
        "activation_command": _activation_command(row.auth_code),
        "message": "Токен сохранён. Отправьте /start с кодом активации боту.",
    }


class WebAppUrlRequest(BaseModel):
    web_app_url: str


@router.put("/settings/telegram/web-app-url")
async def set_web_app_url(body: WebAppUrlRequest, db: AsyncSession = Depends(get_db)):
    """Сохранить web_app_url в TelegramSettings. Пустая строка очищает (fallback на .env)."""
    url = body.web_app_url.strip()
    if url and not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="URL должен быть HTTPS или пустым")
    row = await get_or_create_telegram_settings(db)
    row.web_app_url = url or None
    await db.commit()
    logger.info("web_app_url обновлён: %s", url or "(сброс на .env)")
    return {"ok": True, "web_app_url": url}


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
        await db.commit()
    return {"status": "ok"}
