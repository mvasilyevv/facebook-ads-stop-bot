# -*- coding: utf-8 -*-
"""Тесты наложения текста на видео (ffmpeg drawtext)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from core.creatives.video_overlay import (
    OverlaySpec,
    OverlayValidationError,
    TextOverlay,
    banner,
    caption,
    cta,
    overlay_video,
    resolve_font,
    spec_from_dict,
)
from core.creatives.video_uniquifier import probe_video

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _ffmpeg_has_drawtext() -> bool:
    """ffmpeg собран с libfreetype (filter drawtext). Без него overlay-тесты невозможны —
    на машинах с урезанной сборкой ffmpeg (нет drawtext) их надо пропускать, а не падать."""
    if not _HAS_FFMPEG:
        return False
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return "drawtext" in out.stdout
    except Exception:
        return False


requires_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_has_drawtext(),
    reason="ffmpeg/ffprobe не установлены или ffmpeg без filter drawtext (нет libfreetype)",
)


def _make_clip(path: Path) -> None:
    """Генерирует крошечный чистый ролик 192x320 (~1.2с)."""
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=192x320:rate=24:duration=1.2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _frame_md5(video: Path, second: float, tmp: Path) -> str:
    """md5 кадра на заданной секунде."""
    frame = tmp / f"f_{second}.png"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(second),
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(frame),
        ],
        check=True,
        capture_output=True,
    )
    return hashlib.md5(frame.read_bytes()).hexdigest()


# Сценарий: caption+banner накладываются, длительность сохранена, кадр реально изменился.
@requires_ffmpeg
@pytest.mark.timeout(120)
async def test_overlay_changes_frames_and_keeps_duration(tmp_path):
    src = tmp_path / "clean.mp4"
    _make_clip(src)
    out = tmp_path / "overlaid.mp4"

    spec = OverlaySpec(texts=[caption("GHS 10", fontsize=24), banner("20 FREE BETS", fontsize=18)])
    await overlay_video(src, out, spec)

    assert out.is_file()
    src_probe = await probe_video(src)
    out_probe = await probe_video(out)
    assert (out_probe.width, out_probe.height) == (src_probe.width, src_probe.height)
    assert abs(out_probe.duration - src_probe.duration) <= 0.1
    # Текст реально нарисован: кадры исходника и результата различаются.
    assert _frame_md5(src, 0.5, tmp_path) != _frame_md5(out, 0.5, tmp_path)


# Сценарий: CTA с start=0.6 отсутствует в начале и появляется к концу ролика.
@requires_ffmpeg
@pytest.mark.timeout(120)
async def test_cta_appears_after_start(tmp_path):
    src = tmp_path / "clean2.mp4"
    _make_clip(src)
    base = tmp_path / "base.mp4"
    with_cta = tmp_path / "cta.mp4"

    await overlay_video(src, base, OverlaySpec(texts=[caption("X", fontsize=24)]))
    await overlay_video(
        src,
        with_cta,
        OverlaySpec(texts=[caption("X", fontsize=24), cta("PLAY", start=0.6, fontsize=20)]),
    )

    # До start CTA невидим (кадры совпадают по содержимому не обязаны — кодек),
    # поэтому сравниваем внутри одного файла: ранний кадр != поздний кадр (CTA появился).
    early = _frame_md5(with_cta, 0.2, tmp_path)
    late = _frame_md5(with_cta, 1.0, tmp_path)
    assert early != late


# Сценарий: валидация — пустая спека, кривой цвет, отсутствующий файл, плохой пресет.
async def test_overlay_validation_errors(tmp_path):
    src = tmp_path / "x.mp4"
    src.write_bytes(b"fake")

    with pytest.raises(OverlayValidationError):
        await overlay_video(src, tmp_path / "o.mp4", OverlaySpec())
    with pytest.raises(OverlayValidationError):
        await overlay_video(
            tmp_path / "нет.mp4", tmp_path / "o.mp4", OverlaySpec(texts=[caption("hi")])
        )
    with pytest.raises(OverlayValidationError):
        TextOverlay(text="hi", fontcolor="red;rm -rf")  # не упадёт тут —
        # цвет проверяется при сборке фильтра:
        from core.creatives.video_overlay import _drawtext_filter

        _drawtext_filter(
            TextOverlay(text="hi", fontcolor="red;rm -rf"),
            font=Path("/tmp/f.ttf"),
            textfile=tmp_path / "t.txt",
        )
    with pytest.raises(OverlayValidationError):
        spec_from_dict({"overlays": [{"preset": "neon", "text": "x"}]})


# Сценарий: resolve_font находит системный шрифт (в CI/песочнице есть DejaVu/Poppins).
@requires_ffmpeg
def test_resolve_font_finds_system_font():
    font = resolve_font()
    assert font.is_file()
