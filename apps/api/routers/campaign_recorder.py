from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from apps.api.schemas import (
    RecorderAnalyzeResponseSchema,
    RecorderEventSchema,
    RecorderStartRequestSchema,
    RecorderStartResponseSchema,
    RecorderStatusResponseSchema,
    RecorderStopResponseSchema,
)
from core.campaign_recorder.analyzer import analyze_session_file
from core.campaign_recorder.cdp_session import CdpConnectionError, CdpSession
from core.campaign_recorder.event_injector import (
    attach_recorder,
    clear_events,
    collect_events,
)
from core.campaign_recorder.session_writer import SessionWriter

router = APIRouter(prefix="/api/campaign-recorder", tags=["campaign-recorder"])
logger = logging.getLogger(__name__)
_active_sessions: dict[str, dict] = {}
_RECORDINGS_DIR = Path("recordings")


@router.post("/start", response_model=RecorderStartResponseSchema)
async def start_recording(body: RecorderStartRequestSchema):
    """Подключиться к Vision CDP и начать запись событий."""
    session_id = str(uuid.uuid4())
    writer = SessionWriter(offer_code=body.offer_code)

    async def _run_session():
        session = CdpSession()
        try:
            async with session.connect() as page:
                context = page.context
                report = await attach_recorder(context, session_id=session_id)
                _active_sessions[session_id]["injection_report"] = report
                _active_sessions[session_id]["target_url"] = page.url
                _active_sessions[session_id]["page"] = page
                if not report.ok:
                    _active_sessions[session_id]["status"] = "error"
                    _active_sessions[session_id]["error"] = (
                        "Ни в один фрейм не удалось инжектить recorder"
                    )
                    return
                _active_sessions[session_id]["status"] = "recording"
                logger.info(
                    "Запись стартовала. session=%s, url=%s, pages_injected=%d",
                    session_id,
                    page.url,
                    report.pages_injected,
                )
                stop_event: asyncio.Event = _active_sessions[session_id]["stop_event"]
                tick = 0
                try:
                    while not stop_event.is_set():
                        await asyncio.sleep(1)
                        tick += 1
                        try:
                            events = await collect_events(context)
                        except Exception as poll_exc:
                            logger.warning("Сбой опроса событий: %s", poll_exc)
                            events = []
                        if events:
                            writer.add_events(events)
                            await clear_events(context)
                        if tick % 10 == 0:
                            logger.info(
                                "recording session=%s pages=%d frames=%d events_total=%d",
                                session_id,
                                len(context.pages),
                                sum(len(p.frames) for p in context.pages),
                                writer.event_count,
                            )
                except Exception as exc:
                    logger.error("Ошибка в цикле записи: %s", exc)
                    _active_sessions[session_id]["status"] = "error"
                    _active_sessions[session_id]["error"] = str(exc)
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
        "injection_report": None,
        "target_url": None,
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
    event_count = writer.event_count
    _active_sessions.pop(session_id, None)
    return RecorderStopResponseSchema(
        session_id=session_id, event_count=event_count, file_path=str(path)
    )


@router.get("/status/{session_id}", response_model=RecorderStatusResponseSchema)
async def get_session_status(session_id: str, tail: int = 30):
    """Возвращает текущее состояние активной сессии записи."""
    entry = _active_sessions.get(session_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Сессия записи не найдена")
    writer: SessionWriter = entry["writer"]
    recent = writer.recent_events(tail)
    events_payload = [
        RecorderEventSchema(
            ts=e.get("ts"),
            type=str(e.get("type") or ""),
            tag=e.get("tag") or None,
            text=(e.get("text") or None),
            value=None if e.get("value") is None else str(e.get("value"))[:120],
            aria_label=e.get("aria_label") or None,
            role=e.get("role") or None,
        )
        for e in recent
    ]
    return RecorderStatusResponseSchema(
        session_id=session_id,
        status=entry.get("status", "unknown"),
        event_count=writer.event_count,
        error=entry.get("error"),
        recent_events=events_payload,
    )


@router.get("/analyze", response_model=RecorderAnalyzeResponseSchema)
async def analyze_last_recording(offer_code: str | None = None):
    """Проанализировать последний JSON-файл записи."""
    recordings_dir = _RECORDINGS_DIR

    def _find_files() -> list[Path]:
        if not recordings_dir.exists():
            return []
        return sorted(
            [
                f
                for f in recordings_dir.glob("*.json")
                if (not offer_code or offer_code.upper() in f.name.upper())
            ],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

    files = await asyncio.to_thread(_find_files)
    if not files:
        if not await asyncio.to_thread(recordings_dir.exists):
            raise HTTPException(status_code=404, detail="Папка recordings не найдена")
        raise HTTPException(status_code=404, detail="Нет файлов записи")
    report = await asyncio.to_thread(analyze_session_file, files[0])
    return RecorderAnalyzeResponseSchema(**report)
