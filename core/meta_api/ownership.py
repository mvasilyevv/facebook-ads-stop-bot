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
- custom_audience → ALLOW (аудитория не привязана к кампании, вне owner-scope).
- create_campaign → скоуп по owner-тегу в ИМЕНИ создаваемой кампании (цели в каталоге нет).
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


# mutation_kind, не требующие owner-проверки (нет привязки к кампании).
_SKIP_KINDS = frozenset({"custom_audience"})


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


async def _resolve_campaign(engine: AsyncEngine, fb_campaign_id: str) -> str | None:
    """fb_campaign_id → campaign_name. None если кампании нет в каталоге."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT campaign_name FROM fb_campaigns WHERE fb_campaign_id = :t"),
                {"t": str(fb_campaign_id)},
            )
        ).first()
    return str(row[0] or "") if row else None


async def _resolve_adset(engine: AsyncEngine, fb_adset_id: str) -> str | None:
    """fb_adset_id → campaign_name. None если адсета нет в каталоге."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT c.campaign_name
                    FROM fb_adsets s
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE s.fb_adset_id = :t
                    """
                ),
                {"t": str(fb_adset_id)},
            )
        ).first()
    return str(row[0] or "") if row else None


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


async def _resolve_campaigns_batch(engine: AsyncEngine, ids: list[str]) -> dict[str, str]:
    """fb_campaign_id[] → {fb_campaign_id: campaign_name}."""
    if not ids:
        return {}
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT fb_campaign_id, campaign_name FROM fb_campaigns "
                    "WHERE fb_campaign_id = ANY(:ids)"
                ),
                {"ids": [str(x) for x in ids]},
            )
        ).all()
    return {str(r[0]): str(r[1] or "") for r in rows}


async def _resolve_adsets_batch(engine: AsyncEngine, ids: list[str]) -> dict[str, str]:
    """fb_adset_id[] → {fb_adset_id: campaign_name}."""
    if not ids:
        return {}
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT s.fb_adset_id, c.campaign_name
                    FROM fb_adsets s
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE s.fb_adset_id = ANY(:ids)
                    """
                ),
                {"ids": [str(x) for x in ids]},
            )
        ).all()
    return {str(r[0]): str(r[1] or "") for r in rows}


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
    if "object_ids" in params or "status" in params:
        ids = [str(x).strip() for x in (params.get("object_ids") or [])]
        object_type = str(params.get("object_type") or "ad").lower()
    else:
        ids = [str(x).strip() for x in (params.get("ad_ids") or [])]
        object_type = "ad"
    ids = [i for i in ids if i]
    if not ids:
        return OwnershipDecision(allowed=False, reason="bulk: пустой список id")

    name_by_id: dict[str, str] = {}
    adname_by_id: dict[str, str] = {}
    if object_type == "campaign":
        name_by_id = await _resolve_campaigns_batch(engine, ids)
    elif object_type == "adset":
        name_by_id = await _resolve_adsets_batch(engine, ids)
    else:
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
            reason=f"bulk: {len(foreign_ids)} чужих id ({object_type})",
            foreign_ids=tuple(foreign_ids),
        )
    if not_found_ids:
        return OwnershipDecision(
            allowed=False,
            reason=f"bulk: {len(not_found_ids)} id не в каталоге ({object_type})",
            not_found=True,
            foreign_ids=tuple(not_found_ids),
        )
    return OwnershipDecision(allowed=True, reason=f"bulk: все {len(ids)} принадлежат owner")


# ====================== публичный API ======================


async def check_mutation_ownership(
    engine: AsyncEngine,
    payload: MetaMutationPayload,
    *,
    owner_tag: str | None,
) -> OwnershipDecision:
    """Решение owner-scoping для Marketing API mutation.

    owner_tag пуст/None → ALLOW (фильтр выключен). custom_audience → ALLOW.
    Остальное диспетчеризуется по уровню цели.
    """
    if not (owner_tag or "").strip():
        return OwnershipDecision(allowed=True, reason="owner_tag не задан — owner-scoping выключен")

    kind = payload.mutation_kind
    if kind in _SKIP_KINDS:
        return OwnershipDecision(allowed=True, reason=f"{kind}: вне owner-scope")

    if kind == "create_campaign":
        name = str(((payload.params or {}).get("campaign") or {}).get("name") or "")
        if campaign_matches_owner(campaign_name=name, ad_name="", owner_tag=owner_tag):
            return OwnershipDecision(allowed=True, reason="create_campaign: имя содержит owner-тег")
        return OwnershipDecision(
            allowed=False,
            reason=f"create_campaign: имя {name!r} не содержит owner-тег",
        )

    if kind in ("pause_ad", "activate_ad", "set_ad_creative"):
        resolved = await _resolve_ad(engine, payload.target_id)
        return _decide_single(resolved, owner_tag, target=payload.target_id, level="ad")

    if kind in ("pause_campaign", "activate_campaign", "duplicate_campaign"):
        cname = await _resolve_campaign(engine, payload.target_id)
        resolved = (cname, "") if cname is not None else None
        return _decide_single(resolved, owner_tag, target=payload.target_id, level="campaign")

    if kind == "set_adset_budget":
        cname = await _resolve_adset(engine, payload.target_id)
        resolved = (cname, "") if cname is not None else None
        return _decide_single(resolved, owner_tag, target=payload.target_id, level="adset")

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
    # валидирует kind в __post_init__). Если добавили 11-й kind и забыли owner-scoping —
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
