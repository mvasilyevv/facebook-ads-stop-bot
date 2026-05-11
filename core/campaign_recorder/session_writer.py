from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)
_DEFAULT_RECORDINGS_DIR = Path("recordings")


class SessionWriter:
    """Накапливает события сессии и записывает в JSON."""

    def __init__(self, offer_code: str, recordings_dir: Path | None = None) -> None:
        self._offer_code = offer_code.upper().strip()
        self._dir = (recordings_dir or _DEFAULT_RECORDINGS_DIR).expanduser().resolve()
        self._events: list[dict] = []
        self._started_at = datetime.now(UTC)

    def add_events(self, events: list[dict]) -> None:
        """Добавляет пачку событий в буфер."""
        self._events.extend(events)

    def save(self) -> Path:
        """Записывает сессию в файл и возвращает путь."""
        self._dir.mkdir(parents=True, exist_ok=True)
        ts = self._started_at.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{self._offer_code}.json"
        path = self._dir / filename
        payload = {
            "offer_code": self._offer_code,
            "started_at": self._started_at.isoformat(),
            "saved_at": datetime.now(UTC).isoformat(),
            "event_count": len(self._events),
            "events": self._events,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        logger.info("Сессия сохранена: %s (%d событий)", path, len(self._events))
        return path
