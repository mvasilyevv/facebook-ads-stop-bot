# -*- coding: utf-8 -*-
"""Owner-scoping на ПУТИ ИСПОЛНЕНИЯ действий с объявлениями (money-safety).

В ШАРЕННОМ рекламном кабинете owner-scoping раньше применялся только при СОЗДАНИИ
задач (core/meta_api/bulk.py::resolve_owner_ad_ids). Этот модуль добавляет
last-line-of-defense на ИСПОЛНЕНИИ: перед каждой mutation/toggle проверяем, что
объект принадлежит владельцу (owner_tag в названии его кампании), резолвя
target_id → campaign_name из локального каталога (fb_ads / fb_adsets / fb_campaigns).

Контракт:
- Сам модуль возвращает только ФАКТ владения: allowed + not_found (+ список foreign_ids
  для логов). Решение requeue vs fail принимает worker — он знает НАПРАВЛЕНИЕ действия
  (включающее/выключающее) и применяет строгую money-политику:
    * чужое (found, не owner) → всегда permanent fail;
    * своё, но ещё не в каталоге (скан отстал) → выключающее в requeue (скан догонит),
      включающее в fail.
- owner_tag пуст/None → ALLOW (фильтр выключен, консистентно с campaign_matches_owner
  и detect-фильтром observer'а).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.schemas import MetaMutationPayload
from core.observer.queries import campaign_matches_owner, load_observer_config


@dataclass(frozen=True)
class OwnershipDecision:
    """Вердикт owner-scoping для одной задачи.

    allowed     — можно исполнять (своё или фильтр выключен).
    reason      — human-readable причина (для логов и last_error).
    not_found   — цель не найдена в локальном каталоге (скан мог отстать).
    foreign_ids — id, заблокировавшие задачу (чужие или не найденные) — для логов.
    """

    allowed: bool
    reason: str
    not_found: bool = False
    foreign_ids: tuple[str, ...] = ()


async def load_owner_tag(engine: AsyncEngine) -> str | None:
    """owner_campaign_tag из observer_config (единый источник, как load_scanning_enabled).

    Читается per-task без агрессивного кэша — money-критичная настройка должна
    применяться немедленно после смены в UI/TG.
    """
    cfg = await load_observer_config(engine)
    if not cfg:
        return None
    tag = cfg.get("owner_campaign_tag")
    return tag if isinstance(tag, str) else None


# ====================== резолверы campaign_name по уровню цели ======================


async def _resolve_ad(engine: AsyncEngine, fb_ad_id: str) -> tuple[str, str] | None:
    """fb_ad_id → (campaign_name, ad_name). None если ad нет в каталоге."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT c.campaign_name, a.ad_name
                    FROM fb_ads a
                    JOIN fb_adsets s ON s.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE a.fb_ad_id = :t
                    """
                ),
                {"t": str(fb_ad_id)},
            )
        ).first()
    return (str(row[0] or ""), str(row[1] or "")) if row else None


async def _resolve_ads_batch(
    engine: AsyncEngine, fb_ad_ids: list[str]
) -> dict[str, tuple[str, str]]:
    """fb_ad_id[] → {fb_ad_id: (campaign_name, ad_name)}. Отсутствующие в каталоге не попадут."""
    if not fb_ad_ids:
        return {}
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT a.fb_ad_id, c.campaign_name, a.ad_name
                    FROM fb_ads a
                    JOIN fb_adsets s ON s.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE a.fb_ad_id = ANY(:ids)
                    """
                ),
                {"ids": [str(x) for x in fb_ad_ids]},
            )
        ).all()
    return {str(r[0]): (str(r[1] or ""), str(r[2] or "")) for r in rows}


# ====================== вердикты ======================


def _decide_single(
    resolved: tuple[str, str] | None,
    owner_tag: str | None,
    *,
    target: str,
    level: str,
) -> OwnershipDecision:
    """Вердикт для одиночной цели (ad/campaign/adset)."""
    if resolved is None:
        return OwnershipDecision(
            allowed=False,
            reason=f"{level} {target} не найден в каталоге",
            not_found=True,
            foreign_ids=(str(target),),
        )
    campaign_name, ad_name = resolved
    if campaign_matches_owner(campaign_name=campaign_name, ad_name=ad_name, owner_tag=owner_tag):
        return OwnershipDecision(allowed=True, reason=f"{level} {target}: принадлежит owner")
    return OwnershipDecision(
        allowed=False,
        reason=f"{level} {target}: чужая кампания {campaign_name!r}",
        foreign_ids=(str(target),),
    )


async def _check_bulk(
    engine: AsyncEngine, payload: MetaMutationPayload, owner_tag: str | None
) -> OwnershipDecision:
    """Вердикт для bulk_status_change: проверяем КАЖДЫЙ id.

    Строгая политика: задача допускается только если ВСЕ id принадлежат owner.
    Хоть один чужой → reject (foreign). Чужих нет, но есть не найденные → reject
    not_found (worker решит requeue/fail по направлению). Не фильтруем молча —
    наши bulk-задачи формируются из owner-фильтрованного резолва, чужой id в них =
    аномалия, её надо увидеть в логе.
    """
    params = payload.params or {}
    ids = [str(x).strip() for x in (params.get("ad_ids") or [])]
    ids = [i for i in ids if i]
    if not ids:
        return OwnershipDecision(allowed=False, reason="bulk: пустой список id")

    ad_map = await _resolve_ads_batch(engine, ids)
    name_by_id = {k: v[0] for k, v in ad_map.items()}
    adname_by_id = {k: v[1] for k, v in ad_map.items()}

    not_found_ids: list[str] = []
    foreign_ids: list[str] = []
    for oid in ids:
        cname = name_by_id.get(oid)
        if cname is None:
            not_found_ids.append(oid)
            continue
        if not campaign_matches_owner(
            campaign_name=cname, ad_name=adname_by_id.get(oid, ""), owner_tag=owner_tag
        ):
            foreign_ids.append(oid)

    if foreign_ids:
        return OwnershipDecision(
            allowed=False,
            reason=f"bulk: {len(foreign_ids)} чужих ad id",
            foreign_ids=tuple(foreign_ids),
        )
    if not_found_ids:
        return OwnershipDecision(
            allowed=False,
            reason=f"bulk: {len(not_found_ids)} ad id не в каталоге",
            not_found=True,
            foreign_ids=tuple(not_found_ids),
        )
    return OwnershipDecision(allowed=True, reason=f"bulk: все {len(ids)} ad принадлежат owner")


# ====================== публичный API ======================


async def check_mutation_ownership(
    engine: AsyncEngine,
    payload: MetaMutationPayload,
    *,
    owner_tag: str | None,
) -> OwnershipDecision:
    """Решение owner-scoping для Marketing API mutation.

    owner_tag пуст/None → ALLOW (фильтр выключен). Остальное диспетчеризуется
    по уровню цели.
    """
    if not (owner_tag or "").strip():
        return OwnershipDecision(allowed=True, reason="owner_tag не задан — owner-scoping выключен")

    kind = payload.mutation_kind
    if kind in ("pause_ad", "activate_ad"):
        resolved = await _resolve_ad(engine, payload.target_id)
        return _decide_single(resolved, owner_tag, target=payload.target_id, level="ad")

    if kind == "duplicate_adset_structure":
        params = payload.params or {}
        source_ad_id = str(params.get("source_ad_id") or "").strip()
        resolved = await _resolve_ad(engine, source_ad_id)
        source_decision = _decide_single(
            resolved,
            owner_tag,
            target=source_ad_id,
            level="ad",
        )
        if not source_decision.allowed:
            return source_decision

        campaign_names = params.get("campaign_names")
        if not isinstance(campaign_names, list) or not campaign_names:
            return OwnershipDecision(
                allowed=False,
                reason="duplicate_adset_structure: campaign_names отсутствуют",
            )
        for index, campaign_name in enumerate(campaign_names):
            if not isinstance(campaign_name, str) or not campaign_matches_owner(
                campaign_name=campaign_name,
                ad_name="",
                owner_tag=owner_tag,
            ):
                return OwnershipDecision(
                    allowed=False,
                    reason=(
                        f"duplicate_adset_structure: campaign_names[{index}] не содержит owner-tag"
                    ),
                )
        return OwnershipDecision(
            allowed=True,
            reason=(
                f"ad {source_ad_id}: принадлежит owner; "
                f"все campaign_names ({len(campaign_names)}) содержат owner-tag"
            ),
        )

    if kind == "bulk_status_change":
        return await _check_bulk(engine, payload, owner_tag)

    # Все объявленные MUTATION_KINDS покрыты выше. Сюда попасть нельзя (payload
    # валидирует kind в __post_init__). Если добавили новый kind и забыли owner-scoping —
    # money-safe deny: лучше явный отказ, чем тихой пропуск чужого действия.
    return OwnershipDecision(allowed=False, reason=f"owner-scoping не определён для kind={kind}")


async def check_ad_ownership(
    engine: AsyncEngine,
    fb_ad_id: str,
    *,
    owner_tag: str | None,
) -> OwnershipDecision:
    """Решение owner-scoping для DOM toggle (disable/enable) по fb_ad_id."""
    if not (owner_tag or "").strip():
        return OwnershipDecision(allowed=True, reason="owner_tag не задан — owner-scoping выключен")
    resolved = await _resolve_ad(engine, fb_ad_id)
    return _decide_single(resolved, owner_tag, target=fb_ad_id, level="ad")


__all__ = [
    "OwnershipDecision",
    "check_ad_ownership",
    "check_mutation_ownership",
    "load_owner_tag",
]
