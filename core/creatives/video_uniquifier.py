# -*- coding: utf-8 -*-
"""Уникализация видео-креативов: новый md5 + пиксельные микро-изменения через ffmpeg.

Зеркалит подход `uniquifier.py` (картинки), но для mp4. Meta дедуплицирует видео
сначала по md5 файла, затем по perceptual-hash кадров — поэтому меняем и байты,
и пиксели/тайминг, оставаясь ниже порога заметности глазом:

- микрокроп 1-2px со случайной стороны + scale назад (ломает покадровый хэш);
- слабый сдвиг тона (eq: brightness/contrast/saturation/gamma);
- временной шум (noise) ниже визуального порога;
- лёгкий сдвиг скорости setpts/atempo ±0.2-0.5% (сдвигает все таймстампы кадров);
- чистка метаданных (-map_metadata -1) + перекодирование x264 с вариативным CRF.

Требует ffmpeg/ffprobe в PATH. Копии обрабатываются последовательно — параллельный
ffmpeg усиливает memory pressure (см. «Грабли браузера» в creative-gen.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from core.creatives.service import (
    MAX_COPY_COUNT,
    CreativeValidationError,
    _safe_source_stem,
    build_iteration_name,
    default_creatives_root,
)

MAX_VIDEO_COUNT = 20
MAX_VIDEO_BYTES = 500 * 1024 * 1024
# Допуск изменения длительности: speed-jitter ±0.5% + паддинг кодека.
DURATION_TOLERANCE = 0.03


class VideoUniquifyError(ValueError):
    """Ошибка чтения или обработки видео."""


@dataclass(frozen=True)
class VideoProbe:
    """Свойства исходного ролика, нужные для сборки фильтров."""

    width: int
    height: int
    duration: float
    has_audio: bool


@dataclass(frozen=True)
class _CopyParams:
    """Случайные (seeded) микро-параметры одной копии."""

    crop_left: int
    crop_right: int
    crop_top: int
    crop_bottom: int
    brightness: float
    contrast: float
    saturation: float
    gamma: float
    noise_strength: int
    speed: float | None
    volume: float
    crf: int


@dataclass(frozen=True)
class VideoOutputFile:
    """Один сохранённый mp4-файл."""

    copy_index: int
    source_name: str
    output_name: str
    output_path: str
    md5: str


@dataclass(frozen=True)
class VideoUniquifyResult:
    """Результат пакетной уникализации видео."""

    root_dir: str
    iteration_dir: str
    iteration_name: str
    video_count: int
    copy_count: int
    files: list[VideoOutputFile]


def _seed_to_random(seed_text: str) -> random.Random:
    """Создаёт стабильный генератор параметров для одной копии."""
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _make_params(rnd: random.Random, *, speed_jitter: bool) -> _CopyParams:
    """Генерирует микро-параметры копии ниже порога заметности."""
    side = rnd.choice(("left", "right", "top", "bottom", "horizontal", "vertical"))
    px = rnd.randint(1, 2)
    crop_left = px if side in {"left", "horizontal"} else 0
    crop_right = px if side in {"right", "horizontal"} else 0
    crop_top = px if side in {"top", "vertical"} else 0
    crop_bottom = px if side in {"bottom", "vertical"} else 0

    speed: float | None = None
    if speed_jitter:
        magnitude = rnd.uniform(0.002, 0.005)
        speed = 1.0 + magnitude if rnd.random() < 0.5 else 1.0 - magnitude

    return _CopyParams(
        crop_left=crop_left,
        crop_right=crop_right,
        crop_top=crop_top,
        crop_bottom=crop_bottom,
        brightness=rnd.uniform(-0.004, 0.004),
        contrast=1.0 + rnd.uniform(-0.004, 0.004),
        saturation=1.0 + rnd.uniform(-0.005, 0.005),
        gamma=1.0 + rnd.uniform(-0.003, 0.003),
        noise_strength=rnd.randint(1, 2),
        speed=speed,
        volume=1.0 + rnd.uniform(-0.003, 0.003),
        crf=rnd.choice((18, 19, 20)),
    )


def _build_video_filter(params: _CopyParams, probe: VideoProbe) -> str:
    """Собирает -vf цепочку: микрокроп → scale назад → тон → шум → скорость."""
    chain: list[str] = []
    # На сверхмалых роликах кроп пропускаем (как в картиночном уникализаторе).
    if probe.width >= 64 and probe.height >= 64:
        crop_w = params.crop_left + params.crop_right
        crop_h = params.crop_top + params.crop_bottom
        chain.append(f"crop=iw-{crop_w}:ih-{crop_h}:{params.crop_left}:{params.crop_top}")
        chain.append(f"scale={probe.width}:{probe.height}:flags=lanczos")
    chain.append(
        "eq=brightness={b:.4f}:contrast={c:.4f}:saturation={s:.4f}:gamma={g:.4f}".format(
            b=params.brightness, c=params.contrast, s=params.saturation, g=params.gamma
        )
    )
    chain.append(f"noise=alls={params.noise_strength}:allf=t")
    if params.speed is not None:
        chain.append(f"setpts=PTS/{params.speed:.4f}")
    return ",".join(chain)


def _build_audio_filter(params: _CopyParams) -> str:
    """Собирает -af цепочку: темп в синхрон с видео + микро-сдвиг громкости."""
    chain: list[str] = []
    if params.speed is not None:
        chain.append(f"atempo={params.speed:.4f}")
    chain.append(f"volume={params.volume:.4f}")
    return ",".join(chain)


async def _run_tool(args: list[str], *, tool: str) -> bytes:
    """Запускает ffmpeg/ffprobe и возвращает stdout (stderr — в ошибку)."""
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VideoUniquifyError(f"Не найден {tool} — установите ffmpeg (brew install ffmpeg)") from exc

    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise VideoUniquifyError(f"{tool} завершился с ошибкой: {tail}")
    return stdout


async def probe_video(path: Path, *, ffprobe_bin: str = "ffprobe") -> VideoProbe:
    """Читает размеры, длительность и наличие аудио через ffprobe."""
    stdout = await _run_tool(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        tool="ffprobe",
    )
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoUniquifyError(f"ffprobe вернул нечитаемый ответ для «{path.name}»") from exc

    video_stream = next(
        (s for s in payload.get("streams", []) if s.get("codec_type") == "video"), None
    )
    if video_stream is None:
        raise VideoUniquifyError(f"В файле «{path.name}» нет видеопотока")

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    if width < 8 or height < 8:
        raise VideoUniquifyError(f"Видео «{path.name}» слишком маленькое для обработки")

    has_audio = any(s.get("codec_type") == "audio" for s in payload.get("streams", []))
    duration = float(payload.get("format", {}).get("duration") or 0.0)
    return VideoProbe(width=width, height=height, duration=duration, has_audio=has_audio)


async def uniquify_video_file(
    source: Path,
    output: Path,
    *,
    probe: VideoProbe,
    params: _CopyParams,
    ffmpeg_bin: str = "ffmpeg",
) -> None:
    """Перекодирует один ролик с микро-изменениями в output (mp4/H.264)."""
    args = [
        ffmpeg_bin,
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vf",
        _build_video_filter(params, probe),
    ]
    if probe.has_audio:
        args += ["-af", _build_audio_filter(params), "-c:a", "aac", "-b:a", "128k"]
    else:
        args += ["-an"]
    args += [
        "-map_metadata",
        "-1",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(params.crf),
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-movflags",
        "+faststart",
        str(output),
    ]
    await _run_tool(args, tool="ffmpeg")


def _validate_inputs(offer_name: str, copies: int, videos: list[Path]) -> None:
    """Проверяет параметры до запуска ffmpeg."""
    if not offer_name.strip():
        raise CreativeValidationError("Укажите название оффера")
    if copies < 1:
        raise CreativeValidationError("Количество копий должно быть не меньше 1")
    if copies > MAX_COPY_COUNT:
        raise CreativeValidationError(f"Количество копий не должно превышать {MAX_COPY_COUNT}")
    if not videos:
        raise CreativeValidationError("Передайте хотя бы одно видео")
    if len(videos) > MAX_VIDEO_COUNT:
        raise CreativeValidationError(
            f"За один запуск можно обработать не больше {MAX_VIDEO_COUNT} видео"
        )
    for video in videos:
        if not video.is_file():
            raise CreativeValidationError(f"Файл «{video}» не найден")
        if video.stat().st_size == 0:
            raise CreativeValidationError(f"Файл «{video.name}» пустой")
        if video.stat().st_size > MAX_VIDEO_BYTES:
            raise CreativeValidationError(f"Файл «{video.name}» больше лимита 500 МБ")


def _md5_of_file(path: Path) -> str:
    """Считает md5 файла потоково."""
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def uniquify_videos(
    *,
    offer_name: str,
    copies: int,
    videos: list[Path],
    base_dir: Path | None = None,
    now: datetime | None = None,
    speed_jitter: bool = True,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> VideoUniquifyResult:
    """Создаёт папки копий {1..N} и сохраняет уникализированные mp4.

    Раскладка повторяет картиночный uniquify_creatives:
    `<root>/<offer>_<ts>_<K>creo_<N>copies/{1..N}/<stem>_<i>.mp4`.
    """
    _validate_inputs(offer_name, copies, videos)

    root_dir = base_dir or default_creatives_root()
    run_at = now or datetime.now()
    iteration_name = build_iteration_name(
        offer_name=offer_name,
        now=run_at,
        creative_count=len(videos),
        copy_count=copies,
    )
    iteration_dir = root_dir / iteration_name
    temp_dir = root_dir / f".tmp_{iteration_name}_{uuid4().hex}"
    saved_files: list[VideoOutputFile] = []

    probes = [await probe_video(video, ffprobe_bin=ffprobe_bin) for video in videos]

    await asyncio.to_thread(root_dir.mkdir, parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(temp_dir.mkdir, parents=True, exist_ok=False)
        for copy_index in range(1, copies + 1):
            copy_dir = temp_dir / str(copy_index)
            await asyncio.to_thread(copy_dir.mkdir, parents=True, exist_ok=False)

            pairs = zip(videos, probes, strict=True)
            for video_index, (video, probe) in enumerate(pairs, start=1):
                stem = _safe_source_stem(video.name, video_index)
                output_name = f"{stem}_{copy_index}.mp4"
                output_path = copy_dir / output_name
                rnd = _seed_to_random(
                    f"{iteration_name}:{video.name}:{video_index}:{copy_index}"
                )
                params = _make_params(rnd, speed_jitter=speed_jitter)
                await uniquify_video_file(
                    video, output_path, probe=probe, params=params, ffmpeg_bin=ffmpeg_bin
                )
                md5 = await asyncio.to_thread(_md5_of_file, output_path)
                saved_files.append(
                    VideoOutputFile(
                        copy_index=copy_index,
                        source_name=video.name,
                        output_name=output_name,
                        output_path=str(iteration_dir / str(copy_index) / output_name),
                        md5=md5,
                    )
                )

        if await asyncio.to_thread(iteration_dir.exists):
            raise CreativeValidationError(
                f"Папка «{iteration_dir}» уже существует — повторите запуск"
            )
        await asyncio.to_thread(temp_dir.rename, iteration_dir)
    except Exception:
        if await asyncio.to_thread(temp_dir.exists):
            await asyncio.to_thread(shutil.rmtree, temp_dir)
        raise

    return VideoUniquifyResult(
        root_dir=str(root_dir),
        iteration_dir=str(iteration_dir),
        iteration_name=iteration_name,
        video_count=len(videos),
        copy_count=copies,
        files=saved_files,
    )
