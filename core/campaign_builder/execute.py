# -*- coding: utf-8 -*-
"""Реальный исполнитель залива кампании поверх builder.plan_execution_steps.

Раздел 4 дизайна: порядок строго campaign → adsets → upload(MediaUploader) →
creatives → ads через core/meta_api/client.execute_graph_call(ad_account_id=act).
Канал — ExecuteGraphCall изнутри Vision-сессии (как fb_launch.py), медиа — MediaUploader.

Money-инварианты:
- статусы объектов по launch_state (кампания всегда PAUSED; дети ACTIVE при
  campaign_paused, PAUSED при all_paused) — кривой запуск не тратит;
- каждый вызов адресует ЯВНО заданный кабинет (ad_account_id=cfg.account.act) —
  не «активную вкладку Vision», надёжно для мульти-кабинета;
- последовательное создание (не Batch): при падении на середине знаем точно, какие
  объекты уже созданы → PartialCreateError(created_ids). Воркер пишет их в run и
  НЕ ретраит (Batch/повтор = дубль кампании + двойной открут бюджета).

Прогресс-колбэк вызывается после каждого значимого шага — UI стримит его из campaign_run.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.campaign_builder.builder import (
    CampaignSpec,
    CampaignSpec_Block,
    image_creative_body,
    url_tags_of,
    video_creative_body,
)
from core.campaign_builder.config import CampaignBlock, CampaignConfig
from core.campaign_builder.uniquify import (
    ConceptInput,
    UniquifiedAdset,
    block_code_span,
    build_uniquification_plan,
    uniquify_concepts,
)
from core.meta_api.errors import TemporaryError

logger = logging.getLogger(__name__)

# Прогресс-колбэк: получает плоский снимок состояния (stage + счётчики).
ProgressCb = Callable[[dict[str, Any]], Awaitable[None]]


class _GraphClient(Protocol):
    """Минимальный контракт клиента Graph API (для типизации + моков)."""

    async def execute_graph_call(
        self,
        *,
        method: str,
        endpoint: str,
        body_json: Any = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]: ...


class _Uploader(Protocol):
    """Минимальный контракт MediaUploader (для типизации + моков)."""

    async def upload_image(
        self, ad_account_id: str, image_bytes: bytes, *, filename: str = "upload.jpg"
    ) -> str: ...

    async def upload_video_from_bytes(
        self, ad_account_id: str, video_bytes: bytes, *, filename: str = "upload.mp4"
    ) -> str: ...


# ====================== ошибки ======================


class CampaignExecutionError(RuntimeError):
    """Залив провалился. Базовый класс.

    irreversible_attempted — money-флаг: POST создания campaign БЫЛ инициирован до
    падения (ответ Meta мог потеряться при оборванной Vision-сессии/таймауте → объект
    мог реально создаться). Тогда сбой НЕЛЬЗЯ классифицировать как transient/requeue —
    повтор = дубль кампании. См. classify_execution_error.
    """

    irreversible_attempted: bool = False


class PartialCreateError(CampaignExecutionError):
    """Залив упал на середине: часть объектов УЖЕ создана в Meta (нужна ручная чистка).

    created_ids — id уже созданных объектов (campaigns/adsets/creatives/ads).
    failed_step — на каком шаге упало. Воркер пишет это в run и НЕ ретраит (дубли).

    Возникает в двух случаях:
    - подтверждённый partial: хоть один объект вернул id (created непустой);
    - ack-lost: POST создания campaign инициирован, но ответ потерян (created пуст,
      но объект мог родиться в Meta). created_ids тогда содержит пустые списки —
      сигнал «возможен осиротевший объект, проверь Meta вручную» (money-safety).
    """

    def __init__(
        self, message: str, *, created_ids: dict[str, list[str]], failed_step: str
    ) -> None:
        super().__init__(message)
        self.created_ids = created_ids
        self.failed_step = failed_step
        # PartialCreateError по определению означает «необратимый шаг достигнут».
        self.irreversible_attempted = True


# ====================== результат ======================


def _empty_ids() -> dict[str, list[str]]:
    return {"campaigns": [], "adsets": [], "creatives": [], "ads": []}


@dataclass
class ExecutionResult:
    """Итог успешного залива: id всех созданных Meta-объектов."""

    created_meta_ids: dict[str, list[str]] = field(default_factory=_empty_ids)


# ====================== классификация ошибок ======================


def classify_execution_error(exc: BaseException) -> str:
    """Классифицирует ошибку залива для воркера: permanent | transient | partial.

    - partial: PartialCreateError — часть объектов создана (или ack-lost после POST
      campaign), retry = дубль → mark_failed + осиротевшие id на ручную чистку.
    - transient: TemporaryError на шаге ДО инициации создания campaign (сеть/Vision
      легли до отправки POST — объект гарантированно не родился) → requeue.
    - permanent: PermanentError, ValueError (валидация конфига), всё прочее → mark_failed.

    Money-safety (HIGH-2, зеркало meta_api create/duplicate): если POST создания
    campaign БЫЛ инициирован (irreversible_attempted=True), сбой НЕ может быть transient,
    даже если причина — TemporaryError/SessionUnavailable: ответ Meta мог потеряться, а
    кампания реально создаться. Любой такой сбой → не-retry (permanent/partial). Хрупкий
    признак «есть ли created_ids» недостаточен — POST мог пройти без возврата id.
    """
    if isinstance(exc, PartialCreateError):
        return "partial"
    # POST создания campaign инициирован → необратимо, retry запрещён (дубль).
    if isinstance(exc, CampaignExecutionError) and getattr(exc, "irreversible_attempted", False):
        return "permanent"
    if isinstance(exc, TemporaryError):
        return "transient"
    # Разворачиваем обёртку CampaignExecutionError → исходная причина.
    cause = getattr(exc, "__cause__", None)
    if isinstance(exc, CampaignExecutionError) and cause is not None and cause is not exc:
        if isinstance(cause, TemporaryError):
            return "transient"
    return "permanent"


# ====================== прогресс ======================


@dataclass
class _ProgressState:
    """Накопитель прогресса — отдаётся в колбэк плоским снимком."""

    stage: str = "queued"
    campaigns_done: int = 0
    adsets_done: int = 0
    uploads_done: int = 0
    creatives_done: int = 0
    ads_done: int = 0
    total_ads: int = 0
    # Money-флаг: POST создания campaign инициирован (см. CampaignExecutionError).
    # Выставляется ДО самого вызова execute_graph_call(/campaigns) — ответ мог потеряться.
    campaign_create_attempted: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "campaigns_done": self.campaigns_done,
            "adsets_done": self.adsets_done,
            "uploads_done": self.uploads_done,
            "creatives_done": self.creatives_done,
            "ads_done": self.ads_done,
            "total_ads": self.total_ads,
        }


async def _emit(cb: ProgressCb | None, state: _ProgressState) -> None:
    """Best-effort прогресс-колбэк (не роняет залив)."""
    if cb is None:
        return
    try:
        await cb(state.snapshot())
    except Exception:  # noqa: BLE001 — прогресс не должен ронять money-залив
        logger.warning("execute: прогресс-колбэк упал (игнорирую)", exc_info=True)


# ====================== извлечение id ======================


def _extract_id(resp: dict[str, Any], *, what: str) -> str:
    """Достаёт id из ответа Graph API. Пустой id — баг/неожиданный ответ → ошибка."""
    obj_id = resp.get("id") if isinstance(resp, dict) else None
    if not obj_id:
        raise CampaignExecutionError(f"{what}: Meta вернул пустой id (resp={resp!r})")
    return str(obj_id)


# ====================== маппинг spec ↔ config ======================


def _config_block_by_key(cfg: CampaignConfig, key: str) -> CampaignBlock:
    """Находит CampaignBlock конфига по ключу spec-блока (несёт kind для uniquify)."""
    for block in cfg.campaigns:
        if block.key == key:
            return block
    raise CampaignExecutionError(f"в конфиге нет блока с ключом {key!r}")


# ====================== исполнение одной кампании ======================


async def _execute_block(
    cfg: CampaignConfig,
    spec_block: CampaignSpec_Block,
    concepts: list[ConceptInput],
    *,
    client: _GraphClient,
    uploader: _Uploader,
    created: dict[str, list[str]],
    state: _ProgressState,
    on_progress: ProgressCb | None,
    code_start: int = 1,
) -> None:
    """Заливает одну кампанию: campaign → adsets → upload → creatives → ads.

    Мутирует created (накопитель id) — даже при исключении на середине вызывающий
    видит уже созданные объекты. Порядок шагов — из builder.plan_execution_steps.
    code_start — смещение сквозной нумерации кодов (накоплено по предыдущим блокам),
    чтобы коды совпали с превью и были глобально уникальны (sub3-атрибуция).
    """
    act = cfg.account.act
    cfg_block = _config_block_by_key(cfg, spec_block.key)

    # Распределение вариантов по adset'ам (чистая функция). Падает ДО Meta-вызовов,
    # если concepts пуст — осиротевших объектов не будет. copies жёстко = числу
    # adset'ов spec'а: variant[i]→adset[i] совпадает с числом реально создаваемых
    # adset'ов (нет рассинхрона при advanced copies_per_concept).
    plan = build_uniquification_plan(
        cfg, cfg_block, concepts, copies=len(spec_block.adsets), code_start=code_start
    )
    state.total_ads += sum(len(a.ads) for a in plan.adsets)

    # 1) campaign (всегда PAUSED).
    # Money-safety: помечаем «POST campaign инициирован» ДО самого вызова — если он
    # упадёт (Vision лёг/таймаут), ответ Meta мог потеряться, а кампания создаться.
    # С этого момента любой сбой = необратимый (см. classify_execution_error).
    state.stage = "creating"
    state.campaign_create_attempted = True
    resp = await client.execute_graph_call(
        method="POST",
        endpoint=f"/{act}/campaigns",
        body_json=spec_block.body,
        ad_account_id=act,
    )
    campaign_id = _extract_id(resp, what="campaign")
    created["campaigns"].append(campaign_id)
    state.campaigns_done += 1
    await _emit(on_progress, state)

    # 2) adsets — по числу копий (= число adset'ов плана). Имена/тела из spec.
    adset_ids: list[str] = []
    for adset_index, spec_adset in enumerate(spec_block.adsets):
        body = dict(spec_adset.body)
        body["campaign_id"] = campaign_id
        resp = await client.execute_graph_call(
            method="POST",
            endpoint=f"/{act}/adsets",
            body_json=body,
            ad_account_id=act,
        )
        adset_id = _extract_id(resp, what=f"adset[{adset_index}]")
        adset_ids.append(adset_id)
        created["adsets"].append(adset_id)
        state.adsets_done += 1
        await _emit(on_progress, state)

    # 3) uniquify (тяжёлое: ffmpeg/PIL) — материализуем байты вариантов.
    state.stage = "uploading"
    await _emit(on_progress, state)
    materialized: list[UniquifiedAdset] = await uniquify_concepts(cfg, cfg_block, concepts, plan)

    # 4) upload media + 5) creatives + 6) ads, adset за adset'ом.
    # adset i = K ads (по 1 на концепт), вариант с copy_index == i.
    for adset_index, mat_adset in enumerate(materialized):
        adset_id = adset_ids[adset_index]
        for ad in mat_adset.ads:
            url_tags = url_tags_of(cfg, ad.code)
            # upload
            if ad.media_kind == "video":
                video_id = await uploader.upload_video_from_bytes(
                    act, ad.media_bytes or b"", filename=f"{ad.code}.mp4"
                )
                state.uploads_done += 1
                await _emit(on_progress, state)
                creative_body = video_creative_body(
                    cfg, name=ad.code, video_id=video_id, thumb_hash="", url_tags=url_tags
                )
                # Без явного thumbnail убираем пустой image_hash → Meta берёт авто-кадр.
                vd = creative_body["object_story_spec"]["video_data"]
                if not vd.get("image_hash"):
                    vd.pop("image_hash", None)
            else:
                image_hash = await uploader.upload_image(
                    act, ad.media_bytes or b"", filename=f"{ad.code}.jpeg"
                )
                state.uploads_done += 1
                await _emit(on_progress, state)
                creative_body = image_creative_body(
                    cfg, name=ad.code, image_hash=image_hash, url_tags=url_tags
                )

            state.stage = "creating"
            resp = await client.execute_graph_call(
                method="POST",
                endpoint=f"/{act}/adcreatives",
                body_json=creative_body,
                ad_account_id=act,
            )
            creative_id = _extract_id(resp, what=f"creative[{ad.code}]")
            created["creatives"].append(creative_id)
            state.creatives_done += 1
            await _emit(on_progress, state)

            # ad: статус по launch_state (берём из spec-adset, он уже посчитан).
            ad_status = spec_block.adsets[adset_index].status
            ad_body_payload = {
                "name": ad.code,
                "adset_id": adset_id,
                "creative": {"creative_id": creative_id},
                "status": ad_status,
            }
            resp = await client.execute_graph_call(
                method="POST",
                endpoint=f"/{act}/ads",
                body_json=ad_body_payload,
                ad_account_id=act,
            )
            ad_id = _extract_id(resp, what=f"ad[{ad.code}]")
            created["ads"].append(ad_id)
            state.ads_done += 1
            await _emit(on_progress, state)


# ====================== публичный execute ======================


async def execute_campaign_spec(
    cfg: CampaignConfig,
    spec: CampaignSpec,
    *,
    concepts_by_campaign: dict[str, list[ConceptInput]],
    client: _GraphClient,
    uploader: _Uploader,
    on_progress: ProgressCb | None = None,
) -> ExecutionResult:
    """Заливает все кампании спеки последовательно. Money-критичный путь.

    concepts_by_campaign — концепты на каждый блок (ключ == CampaignBlock.key).
    Порядок объектов: campaign → adsets → upload → creatives → ads (через spec).
    Статусы по launch_state уже зашиты в spec (кампания PAUSED, дети по состоянию).

    Ошибки:
    - PartialCreateError — если хоть один объект уже создан до падения (нужна чистка).
    - CampaignExecutionError — падение ДО создания любого объекта (можно ретраить,
      если transient — воркер решает по classify_execution_error на оригинале причины).
    """
    created = _empty_ids()
    state = _ProgressState(stage="creating")

    # Сквозная нумерация кодов между блоками: блок B продолжает с номера, на котором
    # кончился блок A. Накопление block_code_span ЗЕРКАЛИТ build_campaign_spec (превью):
    # коды глобально уникальны и совпадают с превью (money-инвариант превью==залив).
    code_start = 1
    for spec_block in spec.campaigns:
        concepts = concepts_by_campaign.get(spec_block.key, [])
        if not concepts:
            # Нет концептов на блок — конфиг невалиден. Если объекты уже созданы
            # в прошлых блоках — это partial. (campaign_create_attempted здесь False:
            # проверка концептов идёт ДО любого POST текущего блока.)
            exc_msg = f"нет концептов для кампании {spec_block.key!r}"
            _raise_for_failure(
                created,
                ValueError(exc_msg),
                failed_step="validate",
                campaign_create_attempted=state.campaign_create_attempted,
            )
        try:
            await _execute_block(
                cfg,
                spec_block,
                concepts,
                client=client,
                uploader=uploader,
                created=created,
                state=state,
                on_progress=on_progress,
                code_start=code_start,
            )
        except PartialCreateError:
            raise
        except Exception as exc:  # noqa: BLE001 — единая точка маршрутизации
            _raise_for_failure(
                created,
                exc,
                failed_step=state.stage,
                campaign_create_attempted=state.campaign_create_attempted,
            )
        # Сдвигаем нумерацию на размер только что залитого блока (= len(concepts)×N adset).
        code_start += block_code_span(len(concepts), len(spec_block.adsets))

    state.stage = "succeeded"
    await _emit(on_progress, state)
    return ExecutionResult(created_meta_ids=created)


def _has_created(created: dict[str, list[str]]) -> bool:
    return any(created[k] for k in created)


def _raise_for_failure(
    created: dict[str, list[str]],
    cause: BaseException,
    *,
    failed_step: str,
    campaign_create_attempted: bool = False,
) -> None:
    """Преобразует исключение залива в Partial/CampaignExecutionError (с цепочкой причин).

    Money-safety (HIGH-2):
    - есть подтверждённые created_ids → PartialCreateError (нельзя ретраить: дубли);
    - POST создания campaign инициирован, но created пуст (ack-lost: ответ потерян,
      объект мог родиться) → тоже PartialCreateError с пустыми списками — orphan на
      ручную проверку, retry запрещён;
    - иначе (падение ДО любого POST) → CampaignExecutionError, причина в __cause__ для
      classify (transient → requeue безопасен: объект гарантированно не создан).
    """
    if _has_created(created):
        raise PartialCreateError(
            f"залив упал на шаге {failed_step!r} (часть объектов создана): {cause!r}",
            created_ids=created,
            failed_step=failed_step,
        ) from cause
    if campaign_create_attempted:
        # ack-lost: POST campaign ушёл, ответа нет → возможен осиротевший объект.
        raise PartialCreateError(
            f"залив упал на шаге {failed_step!r} ПОСЛЕ инициации POST campaign "
            f"(ответ Meta потерян — кампания могла создаться, проверь вручную): {cause!r}",
            created_ids=created,
            failed_step=failed_step,
        ) from cause
    # Ничего не создано и POST campaign не инициирован: causa через __cause__ для classify.
    err = CampaignExecutionError(f"залив упал на шаге {failed_step!r}: {cause!r}")
    err.__cause__ = cause
    raise err from cause
