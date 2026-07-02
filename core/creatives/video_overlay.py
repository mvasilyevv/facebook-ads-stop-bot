# -*- coding: utf-8 -*-
"""Детерминированное наложение текста/PNG на видео через ffmpeg (drawtext + overlay).

Пайплайн «чистое видео»: генератор (syntx) делает ролик БЕЗ текста — текст в видео-моделях
главный источник брака (gibberish). Оверлеи накладываем этим модулем: попиксельная точность,
любые правки текста без перегенерации, одно чистое видео → N текстов/гео.

Возможности v1:
- текстовые оверлеи (drawtext): пресеты caption / banner / cta + произвольные параметры;
- тайминг появления (start) с fade-in (alpha-выражение), исчезновение (end);
- PNG-слои (overlay) — для эмодзи/лого/сложных плашек (рендерятся отдельно: Pillow/HTML);
- текст передаётся через textfile= (временный файл) — никакого экранирования спецсимволов.

Апгрейд-путь для анимированных капшенов (pop-in по словам, караоке) — ASS/libass
(`subtitles=file.ass`), генерация через pysubs2; в v1 не подключено.

⚠️ Глифы: убедись, что шрифт содержит все символы текста (у Poppins нет «→» — пиши «->»).
Эмодзи drawtext НЕ рендерит цветными — эмодзи только через PNG-слой.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from core.creatives.video_uniquifier import VideoUniquifyError, _run_tool, probe_video

# Цвет ffmpeg: имя (white), #RRGGBB, опционально @alpha (black@0.78).
_COLOR_RE = re.compile(r"^[#0-9a-zA-Z]+(@[0-9.]+)?$")

# Кандидаты шрифта по умолчанию (первый существующий): репо → macOS → Linux.
_FONT_CANDIDATES = (
    "assets/fonts/overlay.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


class OverlayValidationError(ValueError):
    """Ошибка параметров оверлея."""


@dataclass(frozen=True)
class TextOverlay:
    """Один текстовый слой drawtext."""

    text: str
    y: str = "top"  # "top" | "center" | "bottom" | int px (строкой тоже ок)
    fontsize: int = 48
    fontcolor: str = "white"
    borderw: int = 0
    bordercolor: str = "black"
    box: bool = False
    boxcolor: str = "black@0.78"
    boxborderw: int = 20
    start: float | None = None  # секунда появления (None = с начала)
    fade_in: float = 0.4  # длительность fade при start
    end: float | None = None  # секунда исчезновения (None = до конца)


@dataclass(frozen=True)
class PngOverlay:
    """PNG-слой (эмодзи/лого/плашка), позиция в px от левого верхнего угла."""

    file: Path
    x: int = 0
    y: int = 0
    start: float | None = None
    end: float | None = None


@dataclass(frozen=True)
class OverlaySpec:
    """Полная спека наложения на один ролик."""

    texts: list[TextOverlay] = field(default_factory=list)
    pngs: list[PngOverlay] = field(default_factory=list)
    font_file: Path | None = None


def caption(text: str, *, y: str = "top", fontsize: int = 54) -> TextOverlay:
    """Пресет: верхний кэпшен — белый жирный с чёрной обводкой."""
    return TextOverlay(text=text, y=y, fontsize=fontsize, fontcolor="white", borderw=4)


def banner(text: str, *, fontsize: int = 40, fontcolor: str = "#FFD400") -> TextOverlay:
    """Пресет: нижний баннер — жёлтый текст на тёмной подложке."""
    return TextOverlay(text=text, y="bottom", fontsize=fontsize, fontcolor=fontcolor, box=True)


def cta(text: str, *, start: float, y: str = "bottom2", fontsize: int = 46) -> TextOverlay:
    """Пресет: CTA-плашка — белый текст на красной подложке, появляется с fade."""
    return TextOverlay(
        text=text,
        y=y,
        fontsize=fontsize,
        fontcolor="white",
        box=True,
        boxcolor="#CC2B1D@0.9",
        boxborderw=18,
        start=start,
    )


def resolve_font(explicit: Path | None = None, *, repo_root: Path | None = None) -> Path:
    """Возвращает первый существующий шрифт из кандидатов."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    root = repo_root or Path.cwd()
    for cand in _FONT_CANDIDATES:
        path = Path(cand)
        candidates.append(path if path.is_absolute() else root / path)
    for path in candidates:
        if path.is_file():
            return path
    raise OverlayValidationError(
        "Не найден шрифт: укажи font_file или положи TTF в assets/fonts/overlay.ttf"
    )


def _y_expr(y: str | int) -> str:
    """Преобразует алиас позиции в выражение drawtext (безопасный whitelist)."""
    if isinstance(y, int) or (isinstance(y, str) and y.lstrip("-").isdigit()):
        return str(y)
    aliases = {
        "top": "130",
        "center": "(h-text_h)/2",
        "bottom": "h-text_h-150",
        "bottom2": "h-text_h-320",  # вторая строка снизу (над banner)
    }
    if y not in aliases:
        raise OverlayValidationError(f"Неизвестная позиция y «{y}» (top/center/bottom/bottom2/px)")
    return aliases[y]


def _check_color(value: str, *, what: str) -> str:
    """Валидирует цвет против инъекций в фильтр-строку."""
    if not _COLOR_RE.match(value):
        raise OverlayValidationError(f"Недопустимый цвет {what}: «{value}»")
    return value


def _alpha_expr(item: TextOverlay) -> str | None:
    """Собирает alpha-выражение для появления/исчезновения (без пользовательского ввода)."""
    if item.start is None and item.end is None:
        return None
    start = 0.0 if item.start is None else float(item.start)
    fade = max(float(item.fade_in), 0.01)
    expr = f"if(lt(t\\,{start:.2f})\\,0\\,min(1\\,(t-{start:.2f})/{fade:.2f}))"
    if item.end is not None:
        expr = f"if(gt(t\\,{float(item.end):.2f})\\,0\\,{expr})"
    return expr


def _drawtext_filter(item: TextOverlay, *, font: Path, textfile: Path) -> str:
    """Собирает один drawtext-фильтр."""
    if not item.text.strip():
        raise OverlayValidationError("Пустой текст оверлея")
    parts = [
        f"fontfile={font}",
        f"textfile={textfile}",
        f"fontsize={int(item.fontsize)}",
        f"fontcolor={_check_color(item.fontcolor, what='fontcolor')}",
        "x=(w-text_w)/2",
        f"y={_y_expr(item.y)}",
    ]
    if item.borderw:
        parts.append(f"borderw={int(item.borderw)}")
        parts.append(f"bordercolor={_check_color(item.bordercolor, what='bordercolor')}")
    if item.box:
        parts.append("box=1")
        parts.append(f"boxcolor={_check_color(item.boxcolor, what='boxcolor')}")
        parts.append(f"boxborderw={int(item.boxborderw)}")
    alpha = _alpha_expr(item)
    if alpha is not None:
        parts.append(f"alpha='{alpha}'")
    return "drawtext=" + ":".join(parts)


def _validate_spec_files(source: Path, spec: OverlaySpec) -> None:
    """Проверяет наличие входных файлов (sync-хелпер для async-функции)."""
    if not spec.texts and not spec.pngs:
        raise OverlayValidationError("Спека пустая: ни текстов, ни PNG-слоёв")
    if not source.is_file():
        raise OverlayValidationError(f"Файл «{source}» не найден")
    for png in spec.pngs:
        if not png.file.is_file():
            raise OverlayValidationError(f"PNG-слой «{png.file}» не найден")


def _enable_expr(start: float | None, end: float | None) -> str | None:
    """Выражение enable= для PNG-слоя."""
    if start is None and end is None:
        return None
    lo = 0.0 if start is None else float(start)
    hi = 10_000.0 if end is None else float(end)
    return f"between(t\\,{lo:.2f}\\,{hi:.2f})"


async def overlay_video(
    source: Path,
    output: Path,
    spec: OverlaySpec,
    *,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> None:
    """Накладывает спеку на ролик: drawtext-цепочка + PNG-слои, аудио копируется как есть."""
    _validate_spec_files(source, spec)

    probe = await probe_video(source, ffprobe_bin=ffprobe_bin)
    font = resolve_font(spec.font_file)

    with tempfile.TemporaryDirectory(prefix="fb_overlay_") as tmp:
        tmp_dir = Path(tmp)
        text_filters: list[str] = []
        for index, item in enumerate(spec.texts):
            textfile = tmp_dir / f"text_{index}.txt"
            textfile.write_text(item.text, encoding="utf-8")
            text_filters.append(_drawtext_filter(item, font=font, textfile=textfile))

        args = [ffmpeg_bin, "-loglevel", "error", "-y", "-i", str(source)]
        for png in spec.pngs:
            args += ["-i", str(png.file)]

        if spec.pngs:
            # filter_complex: сперва PNG-слои поверх видео, затем тексты.
            chain = "[0:v]"
            steps: list[str] = []
            for index, png in enumerate(spec.pngs, start=1):
                out_label = f"[v{index}]"
                enable = _enable_expr(png.start, png.end)
                enable_part = f":enable='{enable}'" if enable else ""
                steps.append(
                    f"{chain}[{index}:v]overlay={int(png.x)}:{int(png.y)}{enable_part}{out_label}"
                )
                chain = out_label
            if text_filters:
                steps.append(f"{chain}{','.join(text_filters)}[vout]")
                chain = "[vout]"
            args += ["-filter_complex", ";".join(steps), "-map", chain]
            if probe.has_audio:
                args += ["-map", "0:a:0"]
        else:
            args += ["-vf", ",".join(text_filters)]

        args += [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
        ]
        args += ["-c:a", "copy"] if probe.has_audio else ["-an"]
        args.append(str(output))
        try:
            await _run_tool(args, tool="ffmpeg")
        except VideoUniquifyError as exc:  # единый тип ошибок ffmpeg-обвязки
            raise OverlayValidationError(str(exc)) from exc


def spec_from_dict(payload: dict, *, base_dir: Path | None = None) -> OverlaySpec:
    """Собирает OverlaySpec из JSON-словаря (см. scripts/overlay_video.py)."""
    base = base_dir or Path.cwd()
    texts: list[TextOverlay] = []
    pngs: list[PngOverlay] = []
    presets = {"caption": caption, "banner": banner, "cta": cta}
    for raw in payload.get("overlays", []):
        kind = raw.get("type", "text")
        if kind == "png":
            file = Path(raw["file"])
            pngs.append(
                PngOverlay(
                    file=file if file.is_absolute() else base / file,
                    x=int(raw.get("x", 0)),
                    y=int(raw.get("y", 0)),
                    start=raw.get("start"),
                    end=raw.get("end"),
                )
            )
            continue
        preset_name = raw.get("preset")
        if preset_name:
            if preset_name not in presets:
                raise OverlayValidationError(f"Неизвестный пресет «{preset_name}»")
            kwargs = {k: v for k, v in raw.items() if k not in {"preset", "text", "type"}}
            texts.append(presets[preset_name](raw["text"], **kwargs))
        else:
            kwargs = {k: v for k, v in raw.items() if k != "type"}
            texts.append(TextOverlay(**kwargs))
    font = payload.get("font_file")
    font_path = Path(font) if font else None
    if font_path is not None and not font_path.is_absolute():
        font_path = base / font_path
    return OverlaySpec(texts=texts, pngs=pngs, font_file=font_path)
