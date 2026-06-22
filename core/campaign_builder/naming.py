# -*- coding: utf-8 -*-
"""Рендер имён кампаний/adset'ов и генерация кодов креативов (OFFER_CRxxx).

Шаблон имени поддерживает плейсхолдеры `{byer}`, `{offer}`, `{type}`, `{date}`,
а литерал `adset.pro` остаётся как есть. Источник правды по неймингу — SOP
(`docs/playbooks/campaign-launch.md`): `{byer} | {offer} | {type} | adset.pro | {date}`.
"""

from __future__ import annotations


def render_name(
    template: str,
    *,
    byer: str,
    offer: str,
    date_label: str,
    type_label: str = "",
) -> str:
    """Подставляет плейсхолдеры в шаблон имени.

    `{type}` опционален — если в шаблоне его нет, аргумент игнорируется.
    Литерал `adset.pro` шаблон содержит явно и не трогается.
    """
    return (
        template.replace("{byer}", byer)
        .replace("{offer}", offer)
        .replace("{type}", type_label)
        .replace("{date}", date_label)
    )


def creative_codes(offer_code: str, *, count: int, prefix: str = "") -> list[str]:
    """Генерирует список кодов креативов OFFER_CRxxx с нумерацией от 1.

    По умолчанию префикс = `{offer_code}_CR` → `GH_CR_CR001`, `GH_CR_CR002`, …
    Код идёт в sub3 трекинга и в имя ad/creative.
    """
    if count < 0:
        raise ValueError(f"count не может быть отрицательным, получено {count}")
    used_prefix = prefix or f"{offer_code}_CR"
    return [f"{used_prefix}{i:03d}" for i in range(1, count + 1)]
