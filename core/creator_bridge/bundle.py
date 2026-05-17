"""Загрузка скомпилированного TS-бандла creator-агента."""

from __future__ import annotations

from pathlib import Path

_DEFAULT_PATH = (
    Path(__file__).resolve().parents[2]
    / "services"
    / "browser-agent"
    / "dist"
    / "creator"
    / "index.js"
)


def load_bundle(path: Path | None = None) -> str:
    """Читает скомпилированный creator-бандл и возвращает JS-код."""
    target = path or _DEFAULT_PATH
    if not target.exists():
        raise FileNotFoundError(
            f"creator bundle не найден: {target}. Запусти `npm run build` в services/browser-agent."
        )
    return target.read_text(encoding="utf-8")
