# -*- coding: utf-8 -*-
"""Загрузчик и валидатор Creative Registry (docs/creatives/*.yaml).

Source of truth по креативам: хуки-атомы, гео, слоты, креативы. Скрипт
scripts/creative_report.py джойнит реестр с трекером и считает ranked-хуки.
Контракт полей — docs/creatives/_schema.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Допустимые значения enum-полей (валидация структуры).
_HOOK_LEVELS = {"geo", "slot", "visual", "text"}
_VERDICTS = {"winner", "works", "testing", "weak", "dead", "unknown"}
_CREATIVE_STATUSES = {"draft", "ready", "live", "paused", "archived"}
_FORMATS = {"static", "video", "ugc_text"}
_REFERENCE_SOURCES = {"own", "ad_library"}

# Корень реестра по умолчанию (репозиторий/docs/creatives).
DEFAULT_REGISTRY_DIR = Path(__file__).resolve().parents[2] / "docs" / "creatives"


@dataclass(frozen=True)
class Hook:
    """Атом-хук, переиспользуемый по id из гео и слотов."""

    id: str
    level: str
    text: str
    type: str
    verdict: str
    geo: str | None = None
    slot: str | None = None
    evidence: str | None = None


@dataclass(frozen=True)
class Idea:
    """Угол под слот — реализует набор хуков."""

    id: str
    desc: str
    type: str
    hooks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reference:
    """Референс: свой или конкурент из Ad Library."""

    id: str
    source: str
    format: str
    why: str
    advertiser: str | None = None
    file: str | None = None
    long_active_days: int | None = None


@dataclass(frozen=True)
class Creative:
    """Готовый/в работе креатив. code == sub3 в трекере (джойн-ключ)."""

    code: str
    format: str
    status: str
    verdict: str
    visual_hooks: tuple[str, ...] = ()
    text_hook: str | None = None
    angle: str | None = None
    inspired_by: str | None = None
    file: str | None = None
    note: str | None = None

    def all_hook_ids(self) -> tuple[str, ...]:
        """Все хуки креатива (визуальные + текстовый), без дублей и пустых."""
        ids = list(self.visual_hooks)
        if self.text_hook:
            ids.append(self.text_hook)
        seen: dict[str, None] = {}
        for hid in ids:
            seen.setdefault(hid, None)
        return tuple(seen.keys())


@dataclass(frozen=True)
class Slot:
    """Слот (оффер) внутри гео."""

    code: str
    geo: str
    offer_code: str
    name: str
    mechanic: str
    ideas: tuple[Idea, ...] = ()
    references: tuple[Reference, ...] = ()
    creatives: tuple[Creative, ...] = ()
    findings: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Geo:
    """Гео: рынок + ссылки на geo-хуки + слоты."""

    code: str
    name: str
    languages: tuple[str, ...] = ()
    geo_hooks: tuple[str, ...] = ()
    market_findings: tuple[dict[str, Any], ...] = ()
    payment: dict[str, Any] = field(default_factory=dict)
    # Производственный профиль гео: визуальный стиль/качество/тон, что заходит в гео
    # (напр. Tier-3 → народное качество > вылизанное). Анализируется до генерации,
    # переиспользуется всеми слотами/батчами. См. docs/creatives/SOP.md (R8).
    production_profile: dict[str, Any] = field(default_factory=dict)
    slots: dict[str, Slot] = field(default_factory=dict)


@dataclass(frozen=True)
class Registry:
    """Полный реестр: хуки + гео (со слотами и креативами)."""

    hooks: dict[str, Hook] = field(default_factory=dict)
    geos: dict[str, Geo] = field(default_factory=dict)

    def all_creatives(self) -> list[Creative]:
        """Плоский список всех креативов по всем гео/слотам."""
        out: list[Creative] = []
        for geo in self.geos.values():
            for slot in geo.slots.values():
                out.extend(slot.creatives)
        return out

    def find_creative(self, code: str) -> Creative | None:
        """Креатив по code (точное совпадение)."""
        for creative in self.all_creatives():
            if creative.code == code:
                return creative
        return None

    def validate(self) -> list[str]:
        """Проверяет целостность ссылок и enum-значений. Возвращает список ошибок."""
        errors: list[str] = []
        hook_ids = set(self.hooks)

        # Хуки — enum-поля
        for hook in self.hooks.values():
            if hook.level not in _HOOK_LEVELS:
                errors.append(f"hook {hook.id}: level={hook.level!r} вне {_HOOK_LEVELS}")
            if hook.verdict not in _VERDICTS:
                errors.append(f"hook {hook.id}: verdict={hook.verdict!r} вне {_VERDICTS}")

        for geo in self.geos.values():
            # geo_hooks ссылаются на существующие хуки
            for hid in geo.geo_hooks:
                if hid not in hook_ids:
                    errors.append(f"geo {geo.code}: geo_hook {hid!r} не найден в hooks.yaml")

            for slot in geo.slots.values():
                ref_ids = {ref.id for ref in slot.references}
                seen_codes: set[str] = set()

                for idea in slot.ideas:
                    for hid in idea.hooks:
                        if hid not in hook_ids:
                            errors.append(
                                f"{geo.code}/{slot.code} idea {idea.id}: hook {hid!r} не найден"
                            )

                for ref in slot.references:
                    if ref.source not in _REFERENCE_SOURCES:
                        errors.append(
                            f"{geo.code}/{slot.code} ref {ref.id}: source={ref.source!r} невалиден"
                        )
                    if ref.format not in _FORMATS:
                        errors.append(
                            f"{geo.code}/{slot.code} ref {ref.id}: format={ref.format!r} невалиден"
                        )

                for cr in slot.creatives:
                    if cr.code in seen_codes:
                        errors.append(f"{geo.code}/{slot.code}: дубль code {cr.code!r}")
                    seen_codes.add(cr.code)
                    if cr.status not in _CREATIVE_STATUSES:
                        errors.append(f"creative {cr.code}: status={cr.status!r} невалиден")
                    if cr.verdict not in _VERDICTS:
                        errors.append(f"creative {cr.code}: verdict={cr.verdict!r} невалиден")
                    if cr.format not in _FORMATS:
                        errors.append(f"creative {cr.code}: format={cr.format!r} невалиден")
                    for hid in cr.all_hook_ids():
                        if hid not in hook_ids:
                            errors.append(f"creative {cr.code}: hook {hid!r} не найден")
                    if cr.inspired_by and cr.inspired_by not in ref_ids:
                        errors.append(
                            f"creative {cr.code}: inspired_by {cr.inspired_by!r} не найден в references"
                        )
        return errors


def _load_yaml(path: Path) -> dict[str, Any]:
    """Читает YAML-файл в dict (пустой файл → {})."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def _parse_creative(raw: dict[str, Any]) -> Creative:
    return Creative(
        code=str(raw["code"]),
        format=str(raw.get("format", "static")),
        status=str(raw.get("status", "draft")),
        verdict=str(raw.get("verdict", "unknown")),
        visual_hooks=tuple(raw.get("visual_hooks", []) or []),
        text_hook=raw.get("text_hook"),
        angle=raw.get("angle"),
        inspired_by=raw.get("inspired_by"),
        file=raw.get("file"),
        note=raw.get("note"),
    )


def _parse_slot(raw: dict[str, Any]) -> Slot:
    ideas = tuple(
        Idea(
            id=str(i["id"]),
            desc=str(i.get("desc", "")),
            type=str(i.get("type", "")),
            hooks=tuple(i.get("hooks", []) or []),
        )
        for i in raw.get("ideas", []) or []
    )
    references = tuple(
        Reference(
            id=str(r["id"]),
            source=str(r.get("source", "own")),
            format=str(r.get("format", "static")),
            why=str(r.get("why", "")),
            advertiser=r.get("advertiser"),
            file=r.get("file"),
            long_active_days=r.get("long_active_days"),
        )
        for r in raw.get("references", []) or []
    )
    creatives = tuple(_parse_creative(c) for c in raw.get("creatives", []) or [])
    return Slot(
        code=str(raw["code"]),
        geo=str(raw["geo"]),
        offer_code=str(raw.get("offer_code", "")),
        name=str(raw.get("name", raw["code"])),
        mechanic=str(raw.get("mechanic", "")),
        ideas=ideas,
        references=references,
        creatives=creatives,
        findings=tuple(raw.get("findings", []) or []),
    )


def load_registry(base_dir: Path | str = DEFAULT_REGISTRY_DIR) -> Registry:
    """Загружает весь реестр из YAML-файлов в base_dir.

    Структура: hooks.yaml + geo/<GEO>/geo.yaml + geo/<GEO>/slots/<SLOT>.yaml.
    """
    base = Path(base_dir)
    hooks: dict[str, Hook] = {}
    hooks_file = base / "hooks.yaml"
    if hooks_file.exists():
        for h in _load_yaml(hooks_file).get("hooks", []) or []:
            hook = Hook(
                id=str(h["id"]),
                level=str(h.get("level", "")),
                text=str(h.get("text", "")),
                type=str(h.get("type", "")),
                verdict=str(h.get("verdict", "unknown")),
                geo=h.get("geo"),
                slot=h.get("slot"),
                evidence=h.get("evidence"),
            )
            hooks[hook.id] = hook

    geos: dict[str, Geo] = {}
    geo_root = base / "geo"
    if geo_root.is_dir():
        for geo_dir in sorted(p for p in geo_root.iterdir() if p.is_dir()):
            geo_file = geo_dir / "geo.yaml"
            if not geo_file.exists():
                continue
            graw = _load_yaml(geo_file)
            slots: dict[str, Slot] = {}
            slots_dir = geo_dir / "slots"
            if slots_dir.is_dir():
                for slot_file in sorted(slots_dir.glob("*.yaml")):
                    slot = _parse_slot(_load_yaml(slot_file))
                    slots[slot.code] = slot
            geo = Geo(
                code=str(graw.get("code", geo_dir.name)),
                name=str(graw.get("name", geo_dir.name)),
                languages=tuple(graw.get("languages", []) or []),
                geo_hooks=tuple(graw.get("geo_hooks", []) or []),
                market_findings=tuple(graw.get("market_findings", []) or []),
                payment=dict(graw.get("payment", {}) or {}),
                production_profile=dict(graw.get("production_profile", {}) or {}),
                slots=slots,
            )
            geos[geo.code] = geo

    return Registry(hooks=hooks, geos=geos)
