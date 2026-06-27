# -*- coding: utf-8 -*-
"""Автоуникализация концептов и распределение вариантов по adset'ам.

Раздел 3 дизайна (`docs/superpowers/specs/2026-06-22-campaign-creation-service-design.md`):

    Вход: K концептов (фото/видео) + N adset'ов + copies_per_concept (default = N).
    для каждого концепта C:
        variants = uniquify(C, copies=N)   # seed = hash(concept_id, i) → детерминированно
        variants[i] → adset i
    итог: adset i = K ads (1 на концепт), креатив = уникальная копия i.

Детерминированный seed по (concept_id, copy_index) даёт идемпотентный retry: повторный
прогон воркера производит ровно те же байты, без расхождения с уже залитыми объектами.

Сами уникализаторы — из core/creatives (фото: uniquify_image_bytes; видео:
uniquify_video_file/uniquify_videos через ffmpeg). Тяжёлая работа выполняется в воркере
(latency-tolerant), не в API-запросе.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from core.campaign_builder.config import CampaignBlock, CampaignConfig
from core.campaign_builder.naming import creative_codes
from core.creatives.uniquifier import uniquify_image_bytes
from core.creatives.video_uniquifier import (
    _make_params,
    _seed_to_random,
    probe_video,
    uniquify_video_file,
)


@dataclass(frozen=True)
class ConceptInput:
    """Один исходный концепт креатива (загружен через UI, лежит в media store).

    content — сырые байты (фото) или None если используется path (видео на диске).
    path — путь к файлу на диске (видео; ffmpeg работает с файлами). Для фото можно
    оба, приоритет у content.
    """

    concept_id: str
    kind: str  # image | video
    content: bytes | None = None
    path: str | None = None
    filename: str = ""


@dataclass
class UniquifiedAd:
    """Один ad: концепт × копия i → adset i. Несёт код креатива и (после материализации) байты."""

    concept_id: str
    copy_index: int  # == индексу adset'а, в который попадает
    code: str  # OFFER_CRxxx, уникален в пределах прогона
    seed: str  # детерминированный seed для уникализации
    media_bytes: bytes | None = None  # заполняется на материализации
    media_kind: str = "image"  # image | video


@dataclass
class UniquifiedAdset:
    """Один adset с распределёнными по нему ad'ами (по 1 на концепт)."""

    index: int
    name: str
    ads: list[UniquifiedAd] = field(default_factory=list)


@dataclass
class UniquificationPlan:
    """План уникализации: распределение вариантов по adset'ам (без байтов).

    variants_by_concept[concept_id] — список вариантов концепта (по числу копий).
    adsets — финальная раскладка: adset i = K ads (1 на концепт), copy_index == i.
    """

    copies: int
    adsets: list[UniquifiedAdset]
    variants_by_concept: dict[str, list[UniquifiedAd]]


def _resolve_copies(cfg: CampaignConfig, block: CampaignBlock) -> int:
    """Число копий на концепт: явный copies_per_concept или число adset'ов блока."""
    if cfg.copies_per_concept is not None:
        return cfg.copies_per_concept
    return len(block.adsets)


def block_code_span(concept_count: int, copies: int) -> int:  # noqa: ARG001 — copies для сигнатуры
    """Сколько кодов CRxxx занимает один блок: K концептов (ОДИН код на концепт).

    Код креатива = код КОНЦЕПТА, общий для всех его вариантов (adset'ов): одинаковые
    креативы в разных adset'ах носят один код/имя → трекер агрегирует статистику по
    sub3=CRxxx как один креатив. Поэтому span блока = число концептов, а НЕ K×N adset.

    ЕДИНЫЙ источник смещения сквозной нумерации между блоками. Превью
    (build_campaign_spec) и исполнитель (execute_campaign_spec) обязаны накапливать
    code_start через эту функцию одинаково — иначе коды разъедутся (превью==залив).
    """
    return concept_count


def build_code_layout(
    offer_code: str,
    *,
    concept_count: int,
    copies: int,
    prefix: str = "",
    start: int = 1,
) -> list[list[str]]:
    """Единый source-of-truth раскладки кодов креативов (превью == исполнитель).

    ОДИН код на концепт, ОБЩИЙ для всех его вариантов (adset'ов): концепт c → code[c]
    в КАЖДОМ adset'е. Одинаковые креативы в разных adset'ах носят один код/имя →
    трекер агрегирует статистику по sub3. Уникализация (разные пиксели/md5) — отдельно,
    через seed по copy_index; имя/код от неё НЕ зависит. total кодов = K концептов.

    start — первый номер кода блока. При нескольких блоках исполнитель и превью
    передают накопленное смещение (sum block_code_span предыдущих блоков), чтобы коды
    концептов не коллизировали между кампаниями (sub3-атрибуция).

    Возвращает список по adset'ам: result[i][c] — код концепта c в adset'е i. Для всех
    adset'ов i код концепта c одинаков (result[i][c] == result[j][c] == codes[c]).
    """
    if concept_count < 0:
        raise ValueError(f"concept_count не может быть отрицательным, получено {concept_count}")
    if copies < 0:
        raise ValueError(f"copies не может быть отрицательным, получено {copies}")
    codes = creative_codes(offer_code, count=concept_count, prefix=prefix, start=start)
    # adset i = [код концепта 0, концепта 1, ...] — ОДИН код на концепт, общий по adset'ам.
    return [[codes[c] for c in range(concept_count)] for _i in range(copies)]


def _seed_text(cfg: CampaignConfig, concept_id: str, copy_index: int) -> str:
    """Детерминированный seed по (offer, concept_id, copy_index) — идемпотентный retry."""
    return f"{cfg.offer_code}:{concept_id}:{copy_index}"


def build_uniquification_plan(
    cfg: CampaignConfig,
    block: CampaignBlock,
    concepts: list[ConceptInput],
    *,
    copies: int | None = None,
    code_start: int = 1,
) -> UniquificationPlan:
    """Строит план распределения вариантов по adset'ам (чистая функция, без I/O).

    Для каждого концепта — N вариантов (copies). variant[i] → adset i.
    adset i получает по 1 ad от каждого концепта (всего K ads). Код креатива
    OFFER_CRxxx уникален в пределах прогона (глобальная сквозная нумерация).

    copies — явное число вариантов = число adset'ов раскладки. Если None, берётся
    из cfg.copies_per_concept или числа adset'ов блока. Исполнитель передаёт сюда
    реальное число adset'ов spec'а, чтобы раскладка variant[i]→adset[i] совпала с
    числом созданных adset'ов (защита от рассинхрона при advanced copies_per_concept).

    code_start — смещение сквозной нумерации (см. build_code_layout). При нескольких
    блоках в заливе исполнитель передаёт накопленную сумму block_code_span предыдущих
    блоков — коды глобально уникальны, sub3-атрибуция не коллизирует между кампаниями.
    """
    copies = copies if copies is not None else _resolve_copies(cfg, block)
    if copies < 1:
        raise ValueError(f"copies должно быть >= 1, получено {copies}")
    if not concepts:
        raise ValueError("нужен хотя бы один концепт")

    # Сквозная нумерация кодов через единый source-of-truth раскладки
    # (build_code_layout) — гарантирует побитовое совпадение с превью build_campaign_spec.
    # layout[i][c] = код варианта i концепта c.
    layout = build_code_layout(
        cfg.offer_code,
        concept_count=len(concepts),
        copies=copies,
        prefix=cfg.creative_prefix,
        start=code_start,
    )

    variants_by_concept: dict[str, list[UniquifiedAd]] = {}
    for c_index, concept in enumerate(concepts):
        variants: list[UniquifiedAd] = []
        for i in range(copies):
            variants.append(
                UniquifiedAd(
                    concept_id=concept.concept_id,
                    copy_index=i,
                    code=layout[i][c_index],
                    seed=_seed_text(cfg, concept.concept_id, i),
                    media_kind=concept.kind,
                )
            )
        variants_by_concept[concept.concept_id] = variants

    # Раскладка по adset'ам: adset i = вариант i каждого концепта.
    adsets: list[UniquifiedAdset] = []
    for i in range(copies):
        ads_i = [variants_by_concept[c.concept_id][i] for c in concepts]
        adsets.append(UniquifiedAdset(index=i, name="", ads=ads_i))

    return UniquificationPlan(
        copies=copies,
        adsets=adsets,
        variants_by_concept=variants_by_concept,
    )


def _concept_by_id(concepts: list[ConceptInput]) -> dict[str, ConceptInput]:
    return {c.concept_id: c for c in concepts}


async def _uniquify_one_image(
    cfg: CampaignConfig, concept: ConceptInput, ad: UniquifiedAd
) -> bytes:
    """Уникализирует одно фото в отдельном потоке (PIL — sync)."""
    content = concept.content
    if content is None and concept.path:
        content = await asyncio.to_thread(Path(concept.path).read_bytes)
    if not content:
        raise ValueError(f"концепт {concept.concept_id}: нет содержимого для уникализации")
    return await asyncio.to_thread(
        uniquify_image_bytes,
        content,
        source_name=concept.filename or concept.concept_id,
        copy_index=ad.copy_index + 1,
        creative_index=1,
        run_slug=ad.seed,
    )


async def _uniquify_one_video(concept: ConceptInput, ad: UniquifiedAd) -> bytes:
    """Уникализирует одно видео через ffmpeg (детерминированный seed) → байты mp4."""
    if not concept.path:
        raise ValueError(f"концепт {concept.concept_id}: видео требует path к файлу")
    source = Path(concept.path)
    probe = await probe_video(source)
    rnd = _seed_to_random(ad.seed)
    params = _make_params(rnd, speed_jitter=probe.has_audio)
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / f"{ad.code}.mp4"
        await uniquify_video_file(source, out, probe=probe, params=params)
        return await asyncio.to_thread(out.read_bytes)


async def uniquify_concepts(
    cfg: CampaignConfig,
    block: CampaignBlock,
    concepts: list[ConceptInput],
    plan: UniquificationPlan,
) -> list[UniquifiedAdset]:
    """Материализует план: для каждого ad'а считает уникальные байты варианта.

    Возвращает adset'ы плана с заполненными media_bytes. Детерминизм seed
    гарантирует одинаковый результат при retry. Тяжёлое (ffmpeg/PIL) — в потоках.
    """
    by_id = _concept_by_id(concepts)
    for adset in plan.adsets:
        for ad in adset.ads:
            concept = by_id[ad.concept_id]
            if concept.kind == "video":
                ad.media_bytes = await _uniquify_one_video(concept, ad)
                ad.media_kind = "video"
            else:
                ad.media_bytes = await _uniquify_one_image(cfg, concept, ad)
                ad.media_kind = "image"
    return plan.adsets
