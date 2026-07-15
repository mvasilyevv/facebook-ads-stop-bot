# -*- coding: utf-8 -*-
"""Grace-окно «включить и держать до цены лида» (кейс куратора).

Механика: при подтверждении hold-рекомендации (кнопка ereco) в Redis ставится
маркер enable_grace:{fb_ad_id} с TTL. Observer раз в цикл читает ВСЕ маркеры
одним SCAN'ом (не per-ad!) и передаёт карту в pipeline: для объявления под
активным grace срабатывания стоп-правил подавляются (и алерт, и авто-стоп),
пока не выполнится ЛЮБОЕ из условий выхода:
- истёк TTL / время until;
- после фактического включения набран ещё spend_allowance (~1×CPA) — дальше судит CPL.

Важно: это НЕ снуз. Снуз сознательно глушит только TG-алерты (MID-2), а grace —
именно временное «не стопай, даём открутить». Redis-потеря маркера = fail-safe:
правила снова действуют немедленно (деградация в сторону стопа, не пережога).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

GRACE_KEY_PREFIX = "enable_grace:"


@dataclass(frozen=True)
class EnableGrace:
    """Активный grace-маркер одного объявления."""

    until: datetime
    # Кумулятивный дневной spend, до которого держим (≈1×CPA). None — только по времени.
    spend_cap: Decimal | None = None
    # Spend в момент успешного activate. Падение ниже baseline означает reset дня
    # или рассинхрон метрик — fail-safe завершаем grace.
    baseline_spend: Decimal | None = None


def grace_is_active(grace: EnableGrace, *, now: datetime, spend: Decimal | None) -> bool:
    """Действует ли grace: время не вышло И спенд-кап не выбран.

    spend — кумулятивный дневной spend объявления (включает докликовый расход
    того же cabinet-дня — куратору важен порядок ~1×CPA, не копеечная точность).
    spend=None (нет данных) → считаем активным: без метрик стоп всё равно не сработает.
    """
    if grace.until <= now:
        return False
    if grace.baseline_spend is not None and spend is not None and spend < grace.baseline_spend:
        return False
    if grace.spend_cap is not None and spend is not None and spend >= grace.spend_cap:
        return False
    return True


async def set_enable_grace(
    redis_client: Any,
    *,
    fb_ad_id: str,
    grace_seconds: int,
    spend_cap: Decimal | str | None = None,
    baseline_spend: Decimal | str | None = None,
    spend_allowance: Decimal | str | None = None,
) -> bool:
    """Поставить grace-маркер. Best-effort: False при недоступном Redis (не бросает)."""
    if redis_client is None:
        return False
    try:
        baseline = (
            Decimal(str(baseline_spend)) if baseline_spend not in (None, "", "None") else None
        )
        allowance = (
            Decimal(str(spend_allowance)) if spend_allowance not in (None, "", "None") else None
        )
        cap = Decimal(str(spend_cap)) if spend_cap not in (None, "", "None") else None
    except (InvalidOperation, TypeError, ValueError):
        return False
    if baseline is not None and (not baseline.is_finite() or baseline < 0):
        return False
    if allowance is not None:
        if baseline is None or not allowance.is_finite() or allowance <= 0:
            return False
        cap = baseline + allowance
    if cap is not None and (not cap.is_finite() or cap <= 0):
        return False

    until = datetime.now(timezone.utc) + timedelta(seconds=int(grace_seconds))
    payload = json.dumps(
        {
            "until": until.isoformat(),
            "spend_cap": str(cap) if cap is not None else None,
            "baseline_spend": str(baseline) if baseline is not None else None,
        }
    )
    try:
        # TTL с запасом +60с к until: истечение по времени контролирует поле until,
        # TTL — гарантия самоочистки ключей.
        await redis_client.set(f"{GRACE_KEY_PREFIX}{fb_ad_id}", payload, ex=grace_seconds + 60)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("enable_grace: не смог поставить маркер для %s", fb_ad_id, exc_info=True)
        return False


def _parse_grace(raw: str) -> EnableGrace | None:
    """Разобрать JSON-маркер. Битый маркер → None (правила действуют как обычно)."""
    try:
        data = json.loads(raw)
        until = datetime.fromisoformat(str(data["until"]))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        cap_raw = data.get("spend_cap")
        cap = Decimal(str(cap_raw)) if cap_raw not in (None, "", "None") else None
        if cap is not None and (not cap.is_finite() or cap <= 0):
            return None
        baseline_raw = data.get("baseline_spend")
        baseline = Decimal(str(baseline_raw)) if baseline_raw not in (None, "", "None") else None
        if baseline is not None and (not baseline.is_finite() or baseline < 0):
            return None
        return EnableGrace(until=until, spend_cap=cap, baseline_spend=baseline)
    except (ValueError, KeyError, TypeError, InvalidOperation):
        return None


async def load_enable_grace_map(redis_client: Any) -> dict[str, EnableGrace]:
    """Прочитать все grace-маркеры одним проходом (раз в scan-цикл, не per-ad).

    Любая ошибка Redis → пустая карта: fail-safe в сторону обычных стоп-правил.
    """
    if redis_client is None:
        return {}
    out: dict[str, EnableGrace] = {}
    try:
        async for key in redis_client.scan_iter(match=f"{GRACE_KEY_PREFIX}*", count=100):
            key_str = key.decode() if isinstance(key, bytes) else str(key)
            raw = await redis_client.get(key_str)
            if raw is None:
                continue
            raw_str = raw.decode() if isinstance(raw, bytes) else str(raw)
            grace = _parse_grace(raw_str)
            if grace is not None:
                out[key_str[len(GRACE_KEY_PREFIX) :]] = grace
    except Exception:  # noqa: BLE001
        logger.warning("enable_grace: не смог прочитать маркеры из Redis", exc_info=True)
        return {}
    return out


__all__ = [
    "GRACE_KEY_PREFIX",
    "EnableGrace",
    "grace_is_active",
    "load_enable_grace_map",
    "set_enable_grace",
]
