# -*- coding: utf-8 -*-
"""Тесты уникализации видео-креативов (ffmpeg)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.creatives.service import CreativeValidationError
from core.creatives.video_uniquifier import probe_video, uniquify_videos

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

requires_ffmpeg = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe не установлены")


def _make_clip(path: Path, *, with_audio: bool) -> None:
    """Генерирует крошечный тестовый ролик 192x320 (≈0.6с) через lavfi."""
    args = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=192x320:rate=24:duration=0.6",
    ]
    if with_audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.6", "-c:a", "aac", "-shortest"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True, capture_output=True)


def _md5(path: Path) -> str:
    """Считает md5 файла."""
    return hashlib.md5(path.read_bytes()).hexdigest()


def _ffprobe_json(path: Path) -> dict:
    """Возвращает streams/format ролика."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        check=True,
        capture_output=True,
    ).stdout
    return json.loads(out)


# Сценарий: 2 копии ролика с аудио — md5 все разные, размеры/структура папок сохранены.
@requires_ffmpeg
@pytest.mark.timeout(120)
async def test_uniquify_videos_md5_differ_and_layout(tmp_path):
    src = tmp_path / "GH_AVI_CR001.mp4"
    _make_clip(src, with_audio=True)

    result = await uniquify_videos(
        offer_name="GH_AVI",
        copies=2,
        videos=[src],
        base_dir=tmp_path / "out",
    )

    paths = [Path(f.output_path) for f in result.files]
    assert len(paths) == 2
    assert all(p.is_file() for p in paths)
    # Раскладка как у картинок: {1..N}/<stem>_<i>.mp4
    assert paths[0].parent.name == "1" and paths[0].name == "GH_AVI_CR001_1.mp4"
    assert paths[1].parent.name == "2" and paths[1].name == "GH_AVI_CR001_2.mp4"

    hashes = {_md5(src), _md5(paths[0]), _md5(paths[1])}
    assert len(hashes) == 3, "md5 источника и обеих копий должны различаться"

    # Разрешение сохранено, длительность в допуске, метаданные вычищены.
    src_probe = await probe_video(src)
    for path in paths:
        copy_probe = await probe_video(path)
        assert (copy_probe.width, copy_probe.height) == (src_probe.width, src_probe.height)
        assert abs(copy_probe.duration - src_probe.duration) <= src_probe.duration * 0.05
        assert copy_probe.has_audio is True


# Сценарий: ролик без аудио проходит без -af цепочки, на выходе тоже нет аудиопотока.
@requires_ffmpeg
@pytest.mark.timeout(120)
async def test_uniquify_videos_without_audio(tmp_path):
    src = tmp_path / "GH_AVI_CR002.mp4"
    _make_clip(src, with_audio=False)

    result = await uniquify_videos(
        offer_name="GH_AVI",
        copies=1,
        videos=[src],
        base_dir=tmp_path / "out",
    )

    output = Path(result.files[0].output_path)
    streams = _ffprobe_json(output)["streams"]
    assert all(s["codec_type"] != "audio" for s in streams)
    assert _md5(output) != _md5(src)


# Сценарий: --no-speed (speed_jitter=False) — длительность совпадает с исходной точнее.
@requires_ffmpeg
@pytest.mark.timeout(120)
async def test_uniquify_videos_no_speed_keeps_duration(tmp_path):
    src = tmp_path / "GH_AVI_CR003.mp4"
    _make_clip(src, with_audio=False)

    result = await uniquify_videos(
        offer_name="GH_AVI",
        copies=1,
        videos=[src],
        base_dir=tmp_path / "out",
        speed_jitter=False,
    )

    src_probe = await probe_video(src)
    copy_probe = await probe_video(Path(result.files[0].output_path))
    assert abs(copy_probe.duration - src_probe.duration) <= 0.05


# Сценарий: валидация входа — несуществующий файл и нулевые копии отклоняются до ffmpeg.
async def test_uniquify_videos_validation_errors(tmp_path):
    missing = tmp_path / "нет_такого.mp4"
    with pytest.raises(CreativeValidationError):
        await uniquify_videos(offer_name="GH_AVI", copies=1, videos=[missing])

    real = tmp_path / "x.mp4"
    real.write_bytes(b"fake")
    with pytest.raises(CreativeValidationError):
        await uniquify_videos(offer_name="GH_AVI", copies=0, videos=[real])
    with pytest.raises(CreativeValidationError):
        await uniquify_videos(offer_name="   ", copies=1, videos=[real])
