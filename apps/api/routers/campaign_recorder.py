from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from apps.api.schemas import (
    RecorderAnalyzeResponseSchema,
    RecorderStartRequestSchema,
    RecorderStartResponseSchema,
    RecorderStopResponseSchema,
)
from core.campaign_recorder.analyzer import analyze_session_file
from core.campaign_recorder.cdp_session import CdpConnectionError, CdpSession
from core.campaign_recorder.event_injector import collect_events, inject_event_listener
from core.campaign_recorder.session_writer import SessionWriter

router = APIRouter(prefix="/api/campaign-recorder", tags=["campaign-recorder"])
logger = logging.getLogger(__name__)
_active_sessions: dict[str, dict] = {}


@router.post("/start", response_model=RecorderStartResponseSchema)
async def start_recording(body: RecorderStartRequestSchema):
    """Подключиться к Vision CDP и начать запись событий."""
    session_id = str(uuid.uuid4())
    writer = SessionWriter(offer_code=body.offer_code)

    async def _run_session():
        session = CdpSession(cdp_url=body.cdp_url)
        try:
            async with session.connect() as page:
                await inject_event_listener(page)
                _active_sessions[session_id]["page"] = page
                _active_sessions[session_id]["status"] = "recording"
                stop_event: asyncio.Event = _active_sessions[session_id]["stop_event"]
                while not stop_event.is_set():
                    await asyncio.sleep(2)
                    events = await collect_events(page)
                    if events:
                        writer.add_events(events)
        except CdpConnectionError as exc:
            logger.error("Ошибка CDP: %s", exc)
            _active_sessions[session_id]["status"] = "error"
            _active_sessions[session_id]["error"] = str(exc)

    stop_event = asyncio.Event()
    _active_sessions[session_id] = {
        "writer": writer,
        "page": None,
        "stop_event": stop_event,
        "status": "connecting",
        "error": None,
    }
    task = asyncio.create_task(_run_session())
    _active_sessions[session_id]["task"] = task
    return RecorderStartResponseSchema(session_id=session_id, started=True)


@router.post("/stop/{session_id}", response_model=RecorderStopResponseSchema)
async def stop_recording(session_id: str):
    """Остановить запись и сохранить JSON-файл."""
    entry = _active_sessions.get(session_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Сессия записи не найдена")
    entry["stop_event"].set()
    try:
        await asyncio.wait_for(entry["task"], timeout=5.0)
    except asyncio.TimeoutError:
        entry["task"].cancel()
    writer: SessionWriter = entry["writer"]
    path = writer.save()
    event_count = len(writer._events)
    _active_sessions.pop(session_id, None)
    return RecorderStopResponseSchema(
        session_id=session_id, event_count=event_count, file_path=str(path)
    )


@router.get("/analyze", response_model=RecorderAnalyzeResponseSchema)
async def analyze_last_recording(offer_code: str | None = None):
    """Проанализировать последний JSON-файл записи."""
    recordings_dir = Path("recordings")
    if not recordings_dir.exists():
        raise HTTPException(status_code=404, detail="Папка recordings не найдена")
    files = sorted(
        [
            f
            for f in recordings_dir.glob("*.json")
            if (not offer_code or offer_code.upper() in f.name.upper())
        ],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise HTTPException(status_code=404, detail="Нет файлов записи")
    report = analyze_session_file(files[0])
    return RecorderAnalyzeResponseSchema(**report)
