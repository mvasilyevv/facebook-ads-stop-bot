# -*- coding: utf-8 -*-
"""Атомарное подтверждение enable-рекомендации для TG и web."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class RecommendationNotFoundError(Exception):
    """Рекомендация не существует."""


class RecommendationAlreadyPromotedError(Exception):
    """Рекомендация уже израсходована."""


class RecommendationUnsafeStateError(Exception):
    """Текущее состояние объявления больше не допускает активацию."""


@dataclass(frozen=True, slots=True)
class PromotionResult:
    task_id: int
    fb_ad_id: str
    ad_name: str | None


async def promote_enable_recommendation(
    engine: AsyncEngine,
    *,
    recommendation_id: uuid.UUID | str,
    requested_by: str,
    created_by_chat_id: int | None = None,
    auto_mode: bool = False,
) -> PromotionResult:
    """Заблокировать конкретную рекомендацию, revalidate и создать activate_ad.

    Recommendation row, task insert и promoted_to_task_id меняются в одной
    транзакции. Детерминированный idempotency key привязан к UUID рекомендации,
    поэтому новый инцидент того же объявления не конфликтует со старым.
    """
    try:
        rec_id = uuid.UUID(str(recommendation_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RecommendationNotFoundError("Некорректный id рекомендации") from exc

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT er.id,
                           er.ad_id,
                           er.promoted_to_task_id,
                           er.snapshot_metrics,
                           er.recommendation_level,
                           fa.fb_ad_id,
                           fa.ad_name,
                           fc.ad_account_id
                    FROM enable_recommendations er
                    JOIN fb_ads fa ON fa.id = er.ad_id
                    JOIN fb_adsets fas ON fas.id = fa.adset_id
                    JOIN fb_campaigns fc ON fc.id = fas.campaign_id
                    WHERE er.id = :rid
                    FOR UPDATE OF er
                    """
                ),
                {"rid": rec_id},
            )
        ).first()

        if row is None:
            raise RecommendationNotFoundError(f"Рекомендация id={rec_id} не найдена")
        if row.promoted_to_task_id is not None:
            raise RecommendationAlreadyPromotedError(
                f"Рекомендация id={rec_id} уже подтверждена (task_id={row.promoted_to_task_id})"
            )

        # Тот же per-ad mutex берут все create_mutation_task pause/activate writers.
        # После его получения перечитываем state и unfinished pause: конкурентная
        # pause либо уже закоммитилась и видна, либо ждёт завершения этой транзакции.
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": str(row.fb_ad_id)},
        )
        current = (
            await conn.execute(
                text(
                    """
                    SELECT fa.delivery_status,
                           st.alert_state,
                           st.open_state_token,
                           COALESCE((
                               SELECT oc.auto_enable_recommendations
                               FROM observer_config oc
                               WHERE oc.singleton_key = 'default'
                               LIMIT 1
                           ), false) AS auto_enable_recommendations,
                           EXISTS (
                               SELECT 1
                               FROM ad_auto_enable_disabled aed
                               WHERE aed.ad_id = fa.id
                           ) AS auto_enable_opt_out,
                           EXISTS (
                               SELECT 1
                               FROM task_queue tq
                               WHERE tq.task_type = 'meta_api_mutation'
                                 AND tq.status IN ('draft', 'pending', 'running', 'retrying')
                                 AND (
                                     (tq.payload->>'mutation_kind' = 'pause_ad'
                                      AND tq.payload->>'target_id' = fa.fb_ad_id)
                                     OR
                                     (tq.payload->>'mutation_kind' = 'bulk_status_change'
                                      AND LOWER(COALESCE(
                                          tq.payload->'params'->>'action', ''
                                      )) IN ('pause', 'paused', 'off')
                                      AND EXISTS (
                                          SELECT 1
                                          FROM jsonb_array_elements_text(
                                              COALESCE(
                                                  tq.payload->'params'->'ad_ids',
                                                  '[]'::jsonb
                                              )
                                          ) AS bulk_ad(fb_ad_id)
                                          WHERE bulk_ad.fb_ad_id = fa.fb_ad_id
                                      ))
                                 )
                           ) AS has_unfinished_pause,
                           (
                               SELECT am.spend
                               FROM ad_metrics am
                               WHERE am.ad_id = fa.id
                               ORDER BY am.cycle_ts DESC
                               LIMIT 1
                           ) AS latest_spend,
                           r.cpa_threshold AS current_cpa_threshold
                    FROM fb_ads fa
                    JOIN fb_adsets fas ON fas.id = fa.adset_id
                    JOIN fb_campaigns fc ON fc.id = fas.campaign_id
                    LEFT JOIN offer_rules r ON r.offer_id = fc.offer_id
                    LEFT JOIN ad_alert_state st ON st.ad_id = fa.id
                    WHERE fa.id = :aid
                    """
                ),
                {"aid": row.ad_id},
            )
        ).first()
        if current is None:
            raise RecommendationUnsafeStateError("Объявление рекомендации больше не существует")

        alert_state = str(current.alert_state or "").strip().lower()
        if alert_state not in {"stop_sent", "disabled"}:
            raise RecommendationUnsafeStateError(
                "Рекомендация устарела: инцидент уже закрыт или состояние изменилось"
            )
        if bool(current.has_unfinished_pause):
            raise RecommendationUnsafeStateError(
                "Включение отклонено: отключение объявления ещё выполняется"
            )
        if auto_mode:
            if not bool(current.auto_enable_recommendations):
                raise RecommendationUnsafeStateError(
                    "Автовключение отключено глобальным переключателем"
                )
            if str(row.recommendation_level or "").strip().lower() != "ok":
                raise RecommendationUnsafeStateError(
                    "Автоматически исполняются только рекомендации уровня OK"
                )
            if bool(current.auto_enable_opt_out):
                raise RecommendationUnsafeStateError(
                    "Для объявления включено исключение из auto-enable"
                )
            if str(current.delivery_status or "").strip().upper() != "OFF":
                raise RecommendationUnsafeStateError(
                    "Автовключение отклонено: delivery_status объявления не OFF"
                )

        snapshot = dict(row.snapshot_metrics or {})
        expected_incident = str(snapshot.get("incident_open_state_token") or "").strip()
        current_incident = str(current.open_state_token or "").strip()
        if not expected_incident or not current_incident or expected_incident != current_incident:
            raise RecommendationUnsafeStateError(
                "Рекомендация устарела: STOP-инцидент уже изменился"
            )
        hold_until_cpl = bool(snapshot.get("hold_until_cpl"))
        grace_payload: dict[str, str] | None = None
        if hold_until_cpl:
            if alert_state != "disabled" or str(current.delivery_status or "").upper() != "OFF":
                raise RecommendationUnsafeStateError(
                    "Рекомендация куратора устарела: объявление не подтверждено как OFF"
                )
            try:
                approved_cap = Decimal(str(snapshot.get("grace_spend_cap")))
                current_cpa = Decimal(str(current.current_cpa_threshold))
                latest_spend = Decimal(str(current.latest_spend))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise RecommendationUnsafeStateError(
                    "У рекомендации куратора отсутствует корректный лимит grace"
                ) from exc
            if (
                not approved_cap.is_finite()
                or approved_cap <= 0
                or not current_cpa.is_finite()
                or current_cpa <= 0
                or not latest_spend.is_finite()
                or latest_spend < 0
            ):
                raise RecommendationUnsafeStateError(
                    "Рекомендация куратора устарела: нет корректных текущих spend/CPA"
                )
            spend_cap = min(approved_cap, current_cpa)
            if latest_spend >= spend_cap:
                raise RecommendationUnsafeStateError(
                    "Рекомендация куратора устарела: общий spend уже достиг цены лида"
                )
            grace_payload = {"spend_cap": str(spend_cap), "cap_mode": "absolute_daily"}

        params: dict[str, object] = {
            "source": "recommendation",
            "recommendation_id": str(rec_id),
            "ad_id": str(row.ad_id),
        }
        if grace_payload is not None:
            # Это только intent. Redis marker ставит meta_api_worker после
            # подтверждённого успеха внешнего activate_ad.
            params["enable_grace"] = grace_payload

        payload = {
            "mutation_kind": "activate_ad",
            "target_id": str(row.fb_ad_id),
            "params": params,
            "ad_account_id": str(row.ad_account_id) if row.ad_account_id else None,
        }
        task_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload,
                         attempt_count, max_attempts, requested_by, created_by_chat_id)
                    VALUES
                        ('meta_api_mutation', 'pending', :ik, CAST(:pl AS JSONB),
                         0, 5, :rb, :ccid)
                    RETURNING id
                    """
                ),
                {
                    "ik": f"reco:activate_ad:{rec_id}",
                    "pl": json.dumps(payload),
                    "rb": (requested_by or "recommendation")[:64],
                    "ccid": int(created_by_chat_id) if created_by_chat_id is not None else None,
                },
            )
        ).first()
        if task_row is None:  # pragma: no cover — INSERT ... RETURNING invariant
            raise RuntimeError("Не удалось создать activate_ad task")
        task_id = int(task_row[0])

        promoted = await conn.execute(
            text(
                """
                UPDATE enable_recommendations
                SET promoted_to_task_id = :tid
                WHERE id = :rid AND promoted_to_task_id IS NULL
                """
            ),
            {"tid": task_id, "rid": rec_id},
        )
        if (promoted.rowcount or 0) != 1:  # pragma: no cover — row locked above
            raise RuntimeError("Рекомендация изменилась во время подтверждения")

    return PromotionResult(
        task_id=task_id,
        fb_ad_id=str(row.fb_ad_id),
        ad_name=str(row.ad_name) if row.ad_name else None,
    )


__all__ = [
    "PromotionResult",
    "RecommendationAlreadyPromotedError",
    "RecommendationNotFoundError",
    "RecommendationUnsafeStateError",
    "promote_enable_recommendation",
]
