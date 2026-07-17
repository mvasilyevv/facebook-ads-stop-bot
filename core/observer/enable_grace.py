# -*- coding: utf-8 -*-
"""Grace-окно «включить и держать до цены лида» (кейс куратора).

Механика: при подтверждении hold-рекомендации (кнопка ereco) в Redis ставится
маркер enable_grace:{fb_ad_id} с TTL. Observer раз в цикл читает ВСЕ маркеры
одним SCAN'ом (не per-ad!) и передаёт карту в pipeline: для объявления под
активным grace срабатывания стоп-правил подавляются (и алерт, и авто-стоп),
пока не выполнится ЛЮБОЕ из условий выхода:
- истёк TTL / время until;
- cumulative spend объявления достиг абсолютного spend_cap (= CPA оффера).

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
GRACE_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class EnableGrace:
    """Активный grace-маркер одного объявления."""

    until: datetime
    # Абсолютный cumulative daily spend cap, до которого держим (= одобренный CPA).
    spend_cap: Decimal | None = None
    # Spend в момент успешного activate. Падение ниже baseline означает reset дня
    # или рассинхрон метрик — fail-safe завершаем grace.
    baseline_spend: Decimal | None = None
    # Начало cabinet-day, для которого был одобрен расход. Не даёт маркеру ожить
    # после суточного reset, даже если новый spend снова пересёк старый baseline.
    cabinet_day_start: datetime | None = None
    schema_version: int = GRACE_SCHEMA_VERSION


def grace_is_active(
    grace: EnableGrace,
    *,
    now: datetime,
    spend: Decimal | None,
    absolute_spend_cap: Decimal | None = None,
    current_cabinet_day_start: datetime | None = None,
) -> bool:
    """Действует ли grace: время не вышло и cumulative spend ниже CPA.

    spend — кумулятивный дневной spend объявления (включает докликовый расход
    того же cabinet-дня). ``absolute_spend_cap`` — актуальный CPA из offer_rules;
    если CPA позднее снизили, он дополнительно уменьшает одобренный marker cap.

    Нет spend или денежного cap → fail-safe: grace не действует.
    """
    if grace.until <= now:
        return False
    if grace.schema_version != GRACE_SCHEMA_VERSION:
        return False
    if grace.cabinet_day_start is None or current_cabinet_day_start is None:
        return False
    if grace.cabinet_day_start != current_cabinet_day_start:
        return False
    if spend is None or not spend.is_finite() or spend < 0:
        return False
    if grace.baseline_spend is None or not grace.baseline_spend.is_finite():
        return False
    marker_cap = grace.spend_cap
    if marker_cap is None or not marker_cap.is_finite() or marker_cap <= 0:
        return False
    if absolute_spend_cap is None or not absolute_spend_cap.is_finite() or absolute_spend_cap <= 0:
        return False
    effective_cap = min(marker_cap, absolute_spend_cap)
    if spend < grace.baseline_spend:
        return False
    if spend >= effective_cap:
        return False
    return True


async def set_enable_grace(
    redis_client: Any,
    *,
    fb_ad_id: str,
    grace_seconds: int,
    spend_cap: Decimal | str | None = None,
    baseline_spend: Decimal | str | None = None,
    cabinet_day_start: datetime | None = None,
) -> bool:
    """Поставить grace-маркер. Best-effort: False при недоступном Redis (не бросает)."""
    if redis_client is None:
        return False
    try:
        baseline = (
            Decimal(str(baseline_spend)) if baseline_spend not in (None, "", "None") else None
        )
        cap = Decimal(str(spend_cap)) if spend_cap not in (None, "", "None") else None
    except (InvalidOperation, TypeError, ValueError):
        return False
    if baseline is not None and (not baseline.is_finite() or baseline < 0):
        return False
    if baseline is None:
        return False
    if cap is None or not cap.is_finite() or cap <= 0:
        return False
    # Уже выбранный cap нельзя продлевать ещё на один CPA после activation.
    if baseline is not None and baseline >= cap:
        return False
    if cabinet_day_start is None or cabinet_day_start.tzinfo is None:
        return False

    until = datetime.now(timezone.utc) + timedelta(seconds=int(grace_seconds))
    payload = json.dumps(
        {
            "schema_version": GRACE_SCHEMA_VERSION,
            "until": until.isoformat(),
            "spend_cap": str(cap) if cap is not None else None,
            "baseline_spend": str(baseline) if baseline is not None else None,
            "cabinet_day_start": cabinet_day_start.isoformat(),
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
        version = int(data.get("schema_version", 0))
        if version != GRACE_SCHEMA_VERSION:
            return None
        until = datetime.fromisoformat(str(data["until"]))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        cap_raw = data.get("spend_cap")
        cap = Decimal(str(cap_raw)) if cap_raw not in (None, "", "None") else None
        if cap is None or not cap.is_finite() or cap <= 0:
            return None
        baseline_raw = data.get("baseline_spend")
        baseline = Decimal(str(baseline_raw)) if baseline_raw not in (None, "", "None") else None
        if baseline is None or not baseline.is_finite() or baseline < 0 or baseline >= cap:
            return None
        cabinet_day_start = datetime.fromisoformat(str(data["cabinet_day_start"]))
        if cabinet_day_start.tzinfo is None:
            return None
        return EnableGrace(
            until=until,
            spend_cap=cap,
            baseline_spend=baseline,
            cabinet_day_start=cabinet_day_start,
            schema_version=version,
        )
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
    "GRACE_SCHEMA_VERSION",
    "EnableGrace",
    "grace_is_active",
    "load_enable_grace_map",
    "set_enable_grace",
]
