# -*- coding: utf-8 -*-
"""Удаление фона через rembg (опциональная зависимость).

Вырезать персонажа/объект (PNG с альфой) → пересобрать на новом фоне через
core.imaging.ops.composite. rembg тянет onnxruntime + качает модель (~170MB) на
первом вызове. Импорт ленивый: core.imaging работает и без rembg (остальные ops).

⚠️ onnxruntime пока без колёс под Python 3.14 (текущий .venv) → rembg тут не
ставится. Для удаления фона использовать окружение с Python ≤3.12
(`pip install rembg onnxruntime`) либо дождаться 3.14-колёс. Остальной core.imaging
(Pillow) от этого не зависит.
"""

from __future__ import annotations

from pathlib import Path

_SESSION_CACHE: dict[str, object] = {}


def remove_background(
    src: str | Path,
    out: str | Path,
    *,
    model: str = "u2net",
) -> Path:
    """Удалить фон у src → сохранить PNG с прозрачностью в out.

    model — модель rembg (u2net дефолт; u2netp легче; isnet-general-use точнее).
    Бросает RuntimeError с подсказкой, если rembg не установлен.
    """
    try:
        from rembg import new_session, remove
    except ImportError as exc:  # pragma: no cover — зависит от окружения
        raise RuntimeError(
            "rembg не установлен — для удаления фона: pip install rembg onnxruntime"
        ) from exc

    session = _SESSION_CACHE.get(model)
    if session is None:
        session = new_session(model)
        _SESSION_CACHE[model] = session

    src_path = Path(src).expanduser()
    out_path = Path(out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = remove(src_path.read_bytes(), session=session)
    out_path.write_bytes(result)
    return out_path
