# -*- coding: utf-8 -*-
"""Роутер сервиса создания FB-кампаний (campaigns_create).

Money-критично: создаёт записи, по которым воркер заливает кампании в Meta и
тратит рекламный бюджет. Защита — idempotency_key (offer+date+хеш структуры) +
campaign, ad set и ad всегда создаются PAUSED до отдельного ручного review.

Все маршруты под prefix /api (auto-discovery) → /api/tools/campaigns/*.
X-API-Key на write-методах ставит ApiKeyAuthMiddleware (без dev-tools gate).

Endpoints:
    GET/POST/PUT/DELETE /tools/campaigns/presets[/{id}]  — CRUD пресетов
    POST   /tools/campaigns/upload                       — загрузка концептов
    POST   /tools/campaigns/validate                     — dry-run план
    POST   /tools/campaigns/launch                       — создать run + задачу
    GET    /tools/campaigns/runs[/{id}]                  — список / детали
    POST   /tools/campaigns/runs/{id}/abort              — cooperative abort
    POST   /tools/campaigns/runs/{id}/resume             — safe checkpoint resume
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Literal, TypeVar

from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy import text

from apps.api.deps import DepEngine
from apps.api.middleware.api_problem import api_problem_payload
from apps.api.routers.v1.schemas.campaigns_create import (
    AdsetPlanOut,
    CampaignPlanOut,
    LaunchAccountOut,
    LaunchIn,
    LaunchOut,
    PresetIn,
    PresetOut,
    RunCommandOut,
    RunControlOptionOut,
    RunControlsOut,
    RunDetailOut,
    RunProgressOut,
    RunSummaryOut,
    RunTaskOut,
    UploadConceptsOut,
    UploadedConceptOut,
    ValidateIn,
    ValidatePlanOut,
)
from apps.api.schemas.problem import ApiProblem
from core.ad_account_catalog import ad_account_catalog
from core.campaign_builder.account_context import (
    CampaignAccountContext,
    CampaignAccountContextError,
    campaign_account_context_message,
    require_campaign_account_context,
)
from core.campaign_builder.builder import build_campaign_spec, total_code_span
from core.campaign_builder.config import CampaignConfig, ref_media_kind
from core.campaign_builder.creative_ledger import (
    allocate_code_span,
    peek_next_seq,
    reconcile_offer_seq,
)
from core.campaign_drafts import (
    CampaignDraftConflictError,
    CampaignDraftDocument,
    CampaignDraftEnvelope,
    CampaignDraftPutIn,
    CampaignDraftTooLargeError,
    campaign_drafts,
)
from core.commands import (
    CampaignRunControlUnavailableError,
    CampaignRunIdempotencyConflictError,
    CampaignRunNotFoundError,
    CommandService,
    principal_scoped_idempotency_key,
)
from core.commands.campaign_runs import (
    CampaignRunControls,
    campaign_run_controls,
    campaign_task_state,
)
from core.tasks.queue import (
    create_task,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["campaigns"])

# task_type воркера-исполнителя (контракт со стримом campaign_creator_worker).
CAMPAIGN_TASK_TYPE = "campaign_create"

_LaunchValue = TypeVar("_LaunchValue")


@dataclass(frozen=True, slots=True)
class _AccountLaunchAttempt(Generic[_LaunchValue]):
    account_id: str
    value: _LaunchValue | None = None
    error: Exception | None = None


async def _run_account_launches_independently(
    account_ids: tuple[str, ...],
    launch_one: Callable[[str], Awaitable[_LaunchValue]],
) -> list[_AccountLaunchAttempt[_LaunchValue]]:
    """Execute every unique cabinet even when an earlier cabinet is rejected."""

    attempts: list[_AccountLaunchAttempt[_LaunchValue]] = []
    for account_id in dict.fromkeys(account_ids):
        try:
            value = await launch_one(account_id)
        except Exception as exc:  # noqa: BLE001 - isolation is the fan-out contract
            attempts.append(_AccountLaunchAttempt(account_id=account_id, error=exc))
            continue
        attempts.append(_AccountLaunchAttempt(account_id=account_id, value=value))
    return attempts


_RUN_COMMAND_PROBLEM_RESPONSES = {
    200: {
        "model": RunCommandOut,
        "description": "Replayed command lifecycle or immediately confirmed abort",
    },
    401: {"model": ApiProblem, "description": "Authentication failed"},
    403: {"model": ApiProblem, "description": "Owner role required"},
    404: {"model": ApiProblem, "description": "Campaign run not found"},
    409: {"model": ApiProblem, "description": "Command is unsafe or no longer applicable"},
    422: {"model": ApiProblem, "description": "Invalid command input"},
}

# Лимиты загрузки концептов (зеркало tools.py creative-uniquify).
_MAX_TOTAL_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 МБ (видео тяжелее картинок)
_MAX_UPLOAD_FILES = 50
# Размер чанка стримового чтения: не держим весь файл в RAM до cap-check (OOM-защита).
_UPLOAD_CHUNK_BYTES = 1 * 1024 * 1024  # 1 МБ


def _sniff_media_kind(head: bytes) -> str | None:
    """Тип медиа по magic-байтам начала файла: 'video' | 'image' | None (неизвестно).

    Грубый сниффер для защиты от переименованного файла (напр. PNG с расширением .mp4):
    такой концепт пройдёт kind-валидатор конфига (по расширению), но уронит уникализатор
    (ffmpeg на картинке / PIL на видео) уже ПОСЛЕ создания объектов в Meta → орфаны.
    Ловим несовпадение содержимого и расширения ДО любого POST. None — не распознали
    (пропускаем, воркер разберётся; не блокируем легитимные форматы).
    """
    if len(head) < 12:
        return None
    # image
    if head[:3] == b"\xff\xd8\xff":  # JPEG
        return "image"
    if head[:8] == b"\x89PNG\r\n\x1a\n":  # PNG
        return "image"
    if head[:6] in (b"GIF87a", b"GIF89a"):  # GIF
        return "image"
    if head[:2] == b"BM":  # BMP
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":  # WEBP
        return "image"
    # video
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":  # AVI
        return "video"
    if head[4:8] == b"ftyp":  # MP4/MOV/M4V (ISO-BMFF)
        return "video"
    if head[:4] == b"\x1a\x45\xdf\xa3":  # Matroska / WebM (EBML)
        return "video"
    return None


def _campaign_upload_root() -> Path:
    """Корень временных папок загрузки концептов (per-run).

    Env CAMPAIGN_UPLOAD_ROOT переопределяет дефолт (на удалённом хосте — рядом с
    воркером). Дефолт — ~/Documents/FB_Agent_Campaign_Uploads.
    """
    raw = os.environ.get("CAMPAIGN_UPLOAD_ROOT", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / "Documents" / "FB_Agent_Campaign_Uploads"


def _config_upload_dir(config: CampaignConfig) -> Path:
    """Безопасно резолвит config.creo_root внутри campaign upload store."""
    upload_id = (config.creo_root or "").strip()
    if not upload_id:
        raise ValueError("не указан набор загруженных креативов (creo_root)")
    if Path(upload_id).name != upload_id or upload_id in {".", ".."}:
        raise ValueError("некорректный идентификатор набора креативов")
    return _campaign_upload_root() / upload_id


def _validate_uploaded_concepts(config: CampaignConfig) -> None:
    """Проверяет назначенные refs до создания run и любых объектов в Meta.

    UI хранит один ``creo_root`` на весь набор. Если payload смешал refs
    из разных upload-папок, эта проверка отдаёт синхронный 422 на preview/launch,
    вместо обречённой async-задачи в campaign_creator_worker.
    """
    assigned = [(block.key, ref) for block in config.campaigns for ref in block.concept_refs]
    if not assigned:
        raise ValueError("каждой кампании нужен хотя бы один загруженный концепт")

    upload_dir = _config_upload_dir(config)
    if not upload_dir.is_dir():
        raise ValueError(
            "набор загруженных креативов не найден; вернитесь на шаг 5 и загрузите файлы заново"
        )

    for campaign_key, ref in assigned:
        if not ref or Path(ref).name != ref:
            raise ValueError(f"кампания {campaign_key!r}: некорректная ссылка на концепт {ref!r}")
        if not (upload_dir / ref).is_file():
            raise ValueError(
                f"кампания {campaign_key!r}: концепт {ref!r} отсутствует в текущем наборе "
                "креативов; вернитесь на шаг 5 и загрузите файлы заново"
            )


def _safe_filename(name: str, index: int) -> str:
    """Безопасное имя файла внутри upload-папки (без path traversal)."""
    base = Path(name or "").name  # срезает любые ../ компоненты
    cleaned = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE).strip("._-")
    return cleaned or f"concept_{index}"


def _compute_idempotency_key(config: CampaignConfig) -> str:
    """Детерминированный ключ залива: offer + date + хеш структуры (money-safety).

    Один и тот же конфиг → один и тот же ключ → повторный launch не задвоит залив.
    Хешируем канонический JSON конфига (порядок ключей фиксирован).
    """
    canonical_data = config.model_dump(mode="json")
    # Evidence time is persisted for audit but must not let a routine account
    # refresh bypass duplicate-launch protection.  Timezone and currency remain
    # in the digest, so a real account-context change creates a different key.
    account = canonical_data.get("account")
    if isinstance(account, dict):
        account.pop("account_context_observed_at", None)
    canonical = json.dumps(
        canonical_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"campaign:{config.offer_code}:{config.start_date}:{digest}"


async def _require_offer_scope(
    engine: DepEngine,
    *,
    offer_code: str,
    account_context: CampaignAccountContext,
) -> None:
    """Reject a catalog offer/account/currency mismatch before any writes."""

    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT offer.id,
                           rule.cpa_threshold,
                           rule.currency
                    FROM offers AS offer
                    LEFT JOIN offer_rules AS rule ON rule.offer_id = offer.id
                    WHERE offer.code = :offer_code
                    LIMIT 1
                    """
                    ),
                    {"offer_code": offer_code},
                )
            )
            .mappings()
            .first()
        )
        account_is_configured = False
        if row is not None:
            account_is_configured = await ad_account_catalog.offer_has_account(
                conn,
                offer_id=row["id"],
                account_id=account_context.account_id,
            )
    if row is None:
        return

    if not account_is_configured:
        raise HTTPException(
            status_code=409,
            detail="Выбранный кабинет не привязан к офферу",
        )

    rule_currency = str(row["currency"] or "").strip().upper()
    if row["cpa_threshold"] is not None and not rule_currency:
        raise HTTPException(
            status_code=409,
            detail="Валютный контекст CPA оффера не подтверждён",
        )
    if rule_currency and rule_currency != account_context.currency:
        raise HTTPException(
            status_code=409,
            detail="Валюта CPA оффера не совпадает с валютой кабинета",
        )


def _account_context_rejection(exc: CampaignAccountContextError) -> HTTPException:
    """Перевести отказ контекста кабинета в отказ ДО отправки чего-либо в Meta.

    Устаревший снимок и неактивный кабинет — это конфликт с состоянием, а не
    ошибка ввода: оператор ничего не может исправить в форме. Причина уходит
    словами, машинный код причины остаётся в логе.
    """

    context = exc.context
    is_conflict = context.state == "stale" or context.blocked_by_account_status
    logger.info(
        "campaign launch rejected before dispatch: state=%s issue=%s",
        context.state,
        context.issue,
    )
    return HTTPException(
        status_code=409 if is_conflict else 422,
        detail=(
            campaign_account_context_message(context)
            or "Контекст кабинета не подтверждён — запуск заблокирован"
        ),
    )


async def _campaign_config_from_request(
    body: ValidateIn | LaunchIn,
    engine: DepEngine,
) -> CampaignConfig:
    """Resolve server-owned context and build one immutable domain config."""

    try:
        context = await require_campaign_account_context(
            engine,
            account_id=body.config.act_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Некорректный Ad Account ID") from exc
    except CampaignAccountContextError as exc:
        raise _account_context_rejection(exc) from exc

    if context.currency != "USD" or context.currency_exponent != 2:
        raise HTTPException(
            status_code=409,
            detail="Создание кампаний доступно только для кабинета с подтверждённой валютой USD",
        )

    await _require_offer_scope(
        engine,
        offer_code=body.config.offer_code,
        account_context=context,
    )
    assert context.timezone_name is not None
    assert context.currency is not None
    assert context.observed_at is not None
    return body.domain_config(
        timezone_name=context.timezone_name,
        currency=context.currency,
        account_context_observed_at=context.observed_at,
    )


# ─────────────────────────────── owner draft ────────────────────────────────


def _raise_draft_conflict(exc: CampaignDraftConflictError) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Черновик изменился в другой сессии; загрузите актуальную версию",
    ) from exc


@router.get(
    "/tools/campaigns/draft",
    response_model=CampaignDraftEnvelope,
    responses={403: {"model": ApiProblem}, 409: {"model": ApiProblem}},
)
async def get_campaign_draft(response: Response, engine: DepEngine) -> CampaignDraftEnvelope:
    """Return the one owner draft; absence is an explicit null document."""

    response.headers["Cache-Control"] = "no-store"
    async with engine.connect() as conn:
        draft = await campaign_drafts.load(conn)
    return CampaignDraftEnvelope(draft=draft)


@router.put(
    "/tools/campaigns/draft",
    response_model=CampaignDraftDocument,
    responses={403: {"model": ApiProblem}, 409: {"model": ApiProblem}},
)
async def put_campaign_draft(
    body: CampaignDraftPutIn,
    response: Response,
    engine: DepEngine,
) -> CampaignDraftDocument:
    """Create or update the owner draft with optimistic revision CAS."""

    response.headers["Cache-Control"] = "no-store"
    try:
        async with engine.begin() as conn:
            return await campaign_drafts.save(
                conn,
                expected_revision=body.expected_revision,
                state=body.state,
            )
    except CampaignDraftConflictError as exc:
        _raise_draft_conflict(exc)
    except CampaignDraftTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Черновик превышает допустимый размер",
        ) from exc


@router.delete(
    "/tools/campaigns/draft",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={403: {"model": ApiProblem}, 409: {"model": ApiProblem}},
)
async def delete_campaign_draft(
    engine: DepEngine,
    expected_revision: int = Query(ge=0),
) -> None:
    """Delete only the exact owner draft revision."""

    try:
        async with engine.begin() as conn:
            await campaign_drafts.delete(conn, expected_revision=expected_revision)
    except CampaignDraftConflictError as exc:
        _raise_draft_conflict(exc)


# ─────────────────────────────── presets ────────────────────────────────


_PRESET_COLUMNS = """
    id::text AS id, name, countries, age_min, age_max, genders, placements,
    custom_event_type, budget_level, daily_budget,
    bid_strategy, bid_amount, display_link,
    url_tags_template, naming_template,
    created_at::text AS created_at, updated_at::text AS updated_at
"""


def _preset_row_to_out(row) -> PresetOut:
    """sqlalchemy row → PresetOut (jsonb-поля уже dict/list)."""
    data = dict(row._mapping)
    return PresetOut(**data)


@router.get("/tools/campaigns/presets", response_model=list[PresetOut])
async def list_presets(engine: DepEngine) -> list[PresetOut]:
    """Список всех пресетов (новые сверху)."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(f"SELECT {_PRESET_COLUMNS} FROM campaign_preset ORDER BY created_at DESC")
            )
        ).fetchall()
    return [_preset_row_to_out(r) for r in rows]


@router.post("/tools/campaigns/presets", response_model=PresetOut, status_code=201)
async def create_preset(body: PresetIn, engine: DepEngine) -> PresetOut:
    """Создать пресет. 409 при дубле имени (UNIQUE name)."""
    params = body.model_dump()
    params["countries"] = json.dumps(params["countries"])
    params["genders"] = json.dumps(params["genders"])
    params["placements"] = json.dumps(params["placements"])
    async with engine.begin() as conn:
        dup = (
            await conn.execute(
                text("SELECT 1 FROM campaign_preset WHERE name = :name LIMIT 1"),
                {"name": body.name},
            )
        ).first()
        if dup is not None:
            raise HTTPException(status_code=409, detail=f"Пресет с именем {body.name!r} уже есть")
        row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO campaign_preset
                        (name, countries, age_min, age_max, genders, placements,
                         custom_event_type, budget_level, daily_budget,
                         bid_strategy, bid_amount, display_link,
                         url_tags_template, naming_template)
                    VALUES
                        (:name, CAST(:countries AS JSONB), :age_min, :age_max,
                         CAST(:genders AS JSONB), CAST(:placements AS JSONB),
                         :custom_event_type, :budget_level, :daily_budget,
                         :bid_strategy, :bid_amount, :display_link,
                         :url_tags_template, :naming_template)
                    RETURNING """
                    + _PRESET_COLUMNS
                ),
                params,
            )
        ).first()
    return _preset_row_to_out(row)


@router.put("/tools/campaigns/presets/{preset_id}", response_model=PresetOut)
async def update_preset(preset_id: str, body: PresetIn, engine: DepEngine) -> PresetOut:
    """Полное обновление пресета. 404 если нет, 409 при дубле имени."""
    try:
        pid = uuid.UUID(preset_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="preset_id не UUID") from exc

    params = body.model_dump()
    params["countries"] = json.dumps(params["countries"])
    params["genders"] = json.dumps(params["genders"])
    params["placements"] = json.dumps(params["placements"])
    params["pid"] = pid

    async with engine.begin() as conn:
        # Существование проверяем ПЕРВЫМ: 404 на несуществующий id имеет приоритет над
        # 409 по дублю имени (иначе PUT на чужой/несуществующий id с занятым именем врёт 409).
        exists = (
            await conn.execute(
                text("SELECT 1 FROM campaign_preset WHERE id = :pid LIMIT 1"),
                {"pid": pid},
            )
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail=f"Пресет id={preset_id} не найден")
        dup = (
            await conn.execute(
                text("SELECT 1 FROM campaign_preset WHERE name = :name AND id <> :pid LIMIT 1"),
                {"name": body.name, "pid": pid},
            )
        ).first()
        if dup is not None:
            raise HTTPException(status_code=409, detail=f"Пресет с именем {body.name!r} уже есть")
        row = (
            await conn.execute(
                text(
                    """
                    UPDATE campaign_preset SET
                        name=:name,
                        countries=CAST(:countries AS JSONB), age_min=:age_min, age_max=:age_max,
                        genders=CAST(:genders AS JSONB),
                        placements=CAST(:placements AS JSONB),
                        custom_event_type=:custom_event_type,
                        budget_level=:budget_level, daily_budget=:daily_budget,
                        bid_strategy=:bid_strategy, bid_amount=:bid_amount,
                        display_link=:display_link,
                        url_tags_template=:url_tags_template, naming_template=:naming_template,
                        updated_at=NOW()
                    WHERE id=:pid
                    RETURNING """
                    + _PRESET_COLUMNS
                ),
                params,
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Пресет id={preset_id} не найден")
    return _preset_row_to_out(row)


@router.delete("/tools/campaigns/presets/{preset_id}", status_code=204)
async def delete_preset(preset_id: str, engine: DepEngine) -> None:
    """Удалить пресет. FK run→preset с ON DELETE SET NULL (история запусков цела)."""
    try:
        pid = uuid.UUID(preset_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="preset_id не UUID") from exc
    async with engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM campaign_preset WHERE id = :pid"), {"pid": pid}
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=404, detail=f"Пресет id={preset_id} не найден")


# ─────────────────────────────── upload ────────────────────────────────


@router.post("/tools/campaigns/upload", response_model=UploadConceptsOut)
async def upload_concepts(
    files: list[UploadFile] = File(...),
    upload_id: str | None = Form(default=None),
) -> UploadConceptsOut:
    """Загрузка концептов креативов в per-run временную папку на сервере.

    Возвращает upload_id (входит в config.creo_root для воркера) + список refs
    с размерами для превью. Тяжёлая уникализация — в воркере, не здесь.
    """
    if not files:
        raise HTTPException(status_code=422, detail="Нужен хотя бы один файл")
    if len(files) > _MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"Слишком много файлов: {len(files)} > {_MAX_UPLOAD_FILES}",
        )

    root = _campaign_upload_root()
    root.mkdir(parents=True, exist_ok=True)
    requested_id = (upload_id or "").strip()
    if requested_id:
        if re.fullmatch(r"[0-9a-f]{32}", requested_id) is None:
            raise HTTPException(status_code=422, detail="Некорректный upload_id")
        resolved_id = requested_id
        upload_dir = root / resolved_id
        if not upload_dir.is_dir():
            raise HTTPException(
                status_code=422,
                detail="Набор креативов не найден; обновите страницу и загрузите файлы заново",
            )
    else:
        resolved_id = uuid.uuid4().hex
        upload_dir = root / resolved_id

    existing_files = [p for p in upload_dir.iterdir() if p.is_file()] if upload_dir.exists() else []
    if len(existing_files) + len(files) > _MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"В наборе будет слишком много файлов: "
                f"{len(existing_files) + len(files)} > {_MAX_UPLOAD_FILES}"
            ),
        )
    existing_bytes = sum(p.stat().st_size for p in existing_files)

    # Новые файлы сначала проходят полную проверку во staging-папке. Ошибка дозагрузки
    # не удаляет и не повреждает уже загруженный набор.
    staging_dir = root / f".{resolved_id}.{uuid.uuid4().hex}.uploading"
    staging_dir.mkdir(parents=True)
    try:
        concepts = await _stream_uploads_to_dir(
            files,
            staging_dir,
            reserved_names={p.name for p in existing_files},
            initial_total_bytes=existing_bytes,
        )
        upload_dir.mkdir(parents=True, exist_ok=True)
        for concept in concepts:
            (staging_dir / concept.ref).replace(upload_dir / concept.ref)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    new_metadata = {concept.ref: concept for concept in concepts}
    all_concepts: list[UploadedConceptOut] = []
    for path in sorted((p for p in upload_dir.iterdir() if p.is_file()), key=lambda p: p.name):
        uploaded = new_metadata.get(path.name)
        all_concepts.append(
            UploadedConceptOut(
                ref=path.name,
                original_name=uploaded.original_name if uploaded else path.name,
                size_bytes=path.stat().st_size,
                content_type=(
                    uploaded.content_type if uploaded else mimetypes.guess_type(path.name)[0]
                ),
            )
        )
    total_bytes = sum(concept.size_bytes for concept in all_concepts)
    return UploadConceptsOut(
        upload_id=resolved_id,
        upload_dir=str(upload_dir),
        # Возвращаем серверную истину по ВСЕМУ набору. Фронт по ней удаляет stale refs
        # из persisted draft и сохраняет назначения уже существующих файлов.
        concepts=all_concepts,
        added_refs=[concept.ref for concept in concepts],
        total_bytes=total_bytes,
    )


async def _stream_uploads_to_dir(
    files: list[UploadFile],
    upload_dir: Path,
    *,
    reserved_names: set[str] | None = None,
    initial_total_bytes: int = 0,
) -> list[UploadedConceptOut]:
    """Стримит каждый файл по чанкам на диск с cap-check и magic-валидацией типа.

    Money/OOM-инварианты:
    - суммарный размер проверяется ПО ХОДУ чтения (не читаем весь файл в RAM до cap-check);
    - первый чанк сниффится: расширение vs содержимое (переименованный файл → 422 ДО
      создания объектов в Meta, иначе орфаны после падения уникализатора).
    """
    concepts: list[UploadedConceptOut] = []
    total_bytes = initial_total_bytes
    seen: set[str] = set(reserved_names or ())
    for index, upload in enumerate(files):
        fname = _safe_filename(upload.filename or "", index)
        # Гарантируем уникальность имени и при дозагрузке, и после санитизации.
        stem, suffix = Path(fname).stem, Path(fname).suffix
        serial = 2
        while fname in seen:
            fname = f"{stem}_{serial}{suffix}"
            serial += 1
        seen.add(fname)

        dest = upload_dir / fname
        file_bytes = 0
        head_checked = False
        with dest.open("wb") as fh:
            while True:
                chunk = await upload.read(_UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                if not head_checked:
                    head_checked = True
                    declared = ref_media_kind(fname)
                    sniffed = _sniff_media_kind(chunk[:16])
                    if declared is not None and sniffed is not None and declared != sniffed:
                        raise HTTPException(
                            status_code=422,
                            detail=(
                                f"Файл {upload.filename or fname!r}: расширение указывает тип "
                                f"'{declared}', а содержимое — '{sniffed}' (переименованный файл "
                                "уронит уникализатор уже после создания объектов в Meta)"
                            ),
                        )
                file_bytes += len(chunk)
                total_bytes += len(chunk)
                if total_bytes > _MAX_TOTAL_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Суммарный размер файлов превышает "
                            f"{_MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024)} МБ"
                        ),
                    )
                fh.write(chunk)

        concepts.append(
            UploadedConceptOut(
                ref=fname,
                original_name=upload.filename or fname,
                size_bytes=file_bytes,
                content_type=upload.content_type,
            )
        )
    return concepts


# ─────────────────────────────── validate ────────────────────────────────


@router.post("/tools/campaigns/validate", response_model=ValidatePlanOut)
async def validate_config(body: ValidateIn, engine: DepEngine) -> ValidatePlanOut:
    """Dry-run: собирает план (число объектов + нейминг) без создания в Meta.

    Количество концептов берётся только из config.campaigns[*].concept_refs —
    раскладка K концептов × copies совпадает с исполнителем.
    """
    try:
        config = await _campaign_config_from_request(body, engine)
        _validate_uploaded_concepts(config)
        # Превью показывает реалистичные коды: продолжаем нумерацию оффера.
        # peek_next_seq — read-only, счётчик не двигает.
        async with engine.connect() as conn:
            config.code_start = await peek_next_seq(conn, config.offer_code) + 1
        spec = build_campaign_spec(config)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail="Невалидный конфиг кампании") from exc

    campaigns: list[CampaignPlanOut] = []
    total_adsets = 0
    total_ads = 0
    for block in spec.campaigns:
        adsets = [
            AdsetPlanOut(name=a.name, status=a.status, ad_count=len(a.ads)) for a in block.adsets
        ]
        total_adsets += len(adsets)
        total_ads += sum(a.ad_count for a in adsets)
        campaigns.append(
            CampaignPlanOut(
                key=block.key,
                name=block.name,
                status=block.status,
                adsets=adsets,
            )
        )

    return ValidatePlanOut(
        offer_code=spec.offer_code,
        creation_policy=spec.creation_policy,
        copies_per_concept=spec.copies_per_concept,
        campaign_count=len(campaigns),
        adset_count=total_adsets,
        ad_count=total_ads,
        campaigns=campaigns,
        start_date=config.start_date,
        start_time=config.start_time,
        timezone_name=config.account.timezone_name,
        currency=config.account.currency,
        account_context_observed_at=config.account.account_context_observed_at,
    )


# ─────────────────────────────── launch ────────────────────────────────


async def _launch_one_campaign(
    body: LaunchIn,
    engine: DepEngine,
    *,
    account_id: str,
) -> LaunchAccountOut:
    """Create or replay exactly one cabinet-scoped run and worker task."""

    account_body = body.model_copy(
        update={
            "config": body.config.model_copy(update={"act_id": account_id}),
            "ad_account_ids": None,
            "draft_revision": None,
        }
    )
    # Нормализуем канонический плоский вход в доменный CampaignConfig.
    try:
        config = await _campaign_config_from_request(account_body, engine)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Невалидный конфиг кампании") from exc
    # Builder и воркер читают те же concept_refs, поэтому preview и залив совпадают.
    try:
        build_campaign_spec(config)
        _validate_uploaded_concepts(config)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail="Невалидный конфиг кампании") from exc

    ikey = _compute_idempotency_key(config)
    # В БД пишем КАНОНИЧЕСКИЙ доменный снимок (воркер ждёт вложенный CampaignConfig).
    config_json = config.model_dump_json()
    account_context_observed_at = config.model_dump(mode="json")["account"][
        "account_context_observed_at"
    ]

    preset_uuid: uuid.UUID | None = None
    if account_body.preset_id:
        try:
            preset_uuid = uuid.UUID(account_body.preset_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="preset_id не UUID") from exc

    async with engine.begin() as conn:
        # Preset is provenance only. Its values are already copied into config;
        # deleting it between apply and launch must not invalidate that snapshot.
        if preset_uuid is not None:
            preset_exists = (
                await conn.execute(
                    # Lock the provenance row through run INSERT. A concurrent
                    # DELETE then waits and applies ON DELETE SET NULL after
                    # commit instead of racing the FK check.
                    text(
                        "SELECT 1 FROM campaign_preset WHERE id = :preset_id LIMIT 1 FOR KEY SHARE"
                    ),
                    {"preset_id": preset_uuid},
                )
            ).first()
            if preset_exists is None:
                preset_uuid = None
        # Идемпотентность money-safe (HIGH-4): INSERT ... ON CONFLICT DO NOTHING вместо
        # read-then-insert. Два параллельных launch с одним ключом → один создаёт run,
        # второй ловит конфликт (RETURNING пуст) и возвращает СУЩЕСТВУЮЩИЙ run — без
        # 500 и без дубля залива (=без двойного открута бюджета).
        run_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO campaign_run (preset_id, config, status, idempotency_key)
                    VALUES (:preset_id, CAST(:config AS JSONB), 'queued', :ik)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id::text AS id, status
                    """
                ),
                {"preset_id": preset_uuid, "config": config_json, "ik": ikey},
            )
        ).first()

        if run_row is None:
            # Конфликт по idempotency_key — run уже создан (повтор/гонка). Возвращаем его.
            existing = (
                await conn.execute(
                    text(
                        "SELECT id::text AS id, status FROM campaign_run "
                        "WHERE idempotency_key = :ik LIMIT 1"
                    ),
                    {"ik": ikey},
                )
            ).first()
            if existing is None:
                raise RuntimeError("campaign run idempotency conflict without durable run")
            task_row = (
                await conn.execute(
                    text(
                        "SELECT id FROM task_queue WHERE idempotency_key = :ik "
                        "AND task_type = :tt LIMIT 1"
                    ),
                    {"ik": ikey, "tt": CAMPAIGN_TASK_TYPE},
                )
            ).first()
            logger.info("campaign launch idempotent: run_id=%s ikey=%s", existing.id, ikey)
            return LaunchAccountOut(
                account_id=config.account.act_num,
                run_id=existing.id,
                task_id=int(task_row.id) if task_row else None,
                status=existing.status,
                idempotency_key=ikey,
                replayed=True,
            )

        run_id = run_row.id

        # Per-offer нумерация: резервируем диапазон кодов ТОЛЬКО для реально нового
        # run'а (на конфликт-ветке аллокации нет → без gap'ов на повторах). code_start
        # фиксируется в config → воркер и retry берут одни и те же коды (preview==launch).
        span = total_code_span(config)
        # Перед резервом приводим счётчик к реальности: прошлые НЕУДАЧНЫЕ заливы жгли span
        # (allocate) без отката → next_seq инфлировал выше числа реально созданных
        # креативов (коды прыгали на CR059). reconcile опускает его к max из ledger
        # (безопасно — текущий run исключён, других in-flight по офферу нет). Самолечение
        # на следующем же заливе, без ручного вмешательства.
        await reconcile_offer_seq(conn, config.offer_code, exclude_run_id=run_id)
        base = await allocate_code_span(conn, config.offer_code, span)
        # Переписываем весь config через CAST(:cfg AS JSONB) (тот же паттерн, что INSERT
        # выше) — избегаем полиморфной to_jsonb($1), которую asyncpg не типизирует
        # (DatatypeMismatchError: input has type unknown).
        config.code_start = base
        await conn.execute(
            text(
                "UPDATE campaign_run SET config = CAST(:cfg AS JSONB) WHERE id = CAST(:rid AS UUID)"
            ),
            {"cfg": config.model_dump_json(), "rid": run_id},
        )

        # Задача воркера: payload = {run_id}. task_type='campaign_create' (контракт).
        # Тот же idempotency_key — двойная защита от дубля залива. ON CONFLICT DO NOTHING:
        # если задача уже есть (гонка/повтор) — берём существующую, не плодим дубль.
        task_id = await create_task(
            engine,
            task_type=CAMPAIGN_TASK_TYPE,
            idempotency_key=ikey,
            payload={
                "run_id": run_id,
                "account_id": config.account.act_num,
                "currency": config.account.currency,
                "currency_exponent": config.account.currency_exponent,
                "cabinet_timezone": config.account.timezone_name,
                "account_context_observed_at": account_context_observed_at,
            },
            requested_by="api_launch",
            connection=conn,
        )
        if task_id is None:
            existing_task = (
                await conn.execute(
                    text(
                        "SELECT id FROM task_queue WHERE idempotency_key = :ik "
                        "AND task_type = :tt LIMIT 1"
                    ),
                    {"ik": ikey, "tt": CAMPAIGN_TASK_TYPE},
                )
            ).first()
            task_id = int(existing_task.id) if existing_task else None

    logger.info("campaign launch: run_id=%s task_id=%s ikey=%s", run_id, task_id, ikey)
    return LaunchAccountOut(
        account_id=config.account.act_num,
        run_id=run_id,
        task_id=task_id,
        status="queued",
        idempotency_key=ikey,
    )


async def _configured_offer_accounts(engine: DepEngine, *, offer_code: str) -> set[str] | None:
    """Load the only allowed source set for an explicit multi-account request."""

    async with engine.connect() as conn:
        offer_id = await conn.scalar(
            text("SELECT id FROM offers WHERE code = :offer_code LIMIT 1"),
            {"offer_code": offer_code},
        )
        if offer_id is None:
            return None
        grouped = await ad_account_catalog.list_by_offer(conn, offer_ids=(offer_id,))
    return set(grouped.get(offer_id, []))


def _account_launch_error(account_id: str, exc: Exception) -> LaunchAccountOut:
    if isinstance(exc, HTTPException) and isinstance(exc.detail, str):
        detail = exc.detail
    elif isinstance(exc, ValueError):
        detail = str(exc)
    else:
        logger.error(
            "campaign launch failed before enqueue for account=%s error_type=%s",
            account_id,
            type(exc).__name__,
        )
        detail = "Запуск кабинета не поставлен в очередь"
    return LaunchAccountOut(account_id=account_id, status="rejected", error=detail)


async def _clear_launch_draft(
    engine: DepEngine,
    *,
    revision: int | None,
) -> bool:
    if revision is None:
        return False
    async with engine.begin() as conn:
        return await campaign_drafts.clear_if_revision(conn, revision=revision)


@router.post(
    "/tools/campaigns/launch",
    response_model=LaunchOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def launch_campaign(body: LaunchIn, engine: DepEngine) -> LaunchOut:
    """Fan out one operator request into independent cabinet-scoped runs.

    Each accepted cabinet still uses the existing atomic ``campaign_run + task``
    transaction. An explicit multi-account request is restricted to
    ``offer_ad_accounts`` and converts per-cabinet preflight failures into
    per-cabinet receipts instead of rolling back successful siblings.
    """

    # Old clients retain the original fail-fast single-account contract. New
    # clients always send ad_account_ids sourced from the selected catalog offer.
    if body.ad_account_ids is None:
        receipt = await _launch_one_campaign(body, engine, account_id=body.config.act_id)
        draft_cleared = await _clear_launch_draft(engine, revision=body.draft_revision)
        return LaunchOut(
            run_id=receipt.run_id,
            task_id=receipt.task_id,
            status=receipt.status,
            idempotency_key=receipt.idempotency_key,
            draft_cleared=draft_cleared,
            request_state="accepted",
            accounts=[receipt],
        )

    configured_accounts = await _configured_offer_accounts(
        engine,
        offer_code=body.config.offer_code,
    )

    async def launch_selected(account_id: str) -> LaunchAccountOut:
        if configured_accounts is None:
            raise ValueError("Оффер не найден в каталоге")
        if account_id not in configured_accounts:
            raise ValueError("Кабинет не привязан к офферу")
        return await _launch_one_campaign(body, engine, account_id=account_id)

    attempts = await _run_account_launches_independently(
        tuple(body.ad_account_ids),
        launch_selected,
    )
    accounts = [
        attempt.value
        if attempt.error is None and attempt.value is not None
        else _account_launch_error(attempt.account_id, attempt.error or RuntimeError())
        for attempt in attempts
    ]
    accepted = [account for account in accounts if account.run_id is not None]
    request_state: Literal["accepted", "partial", "rejected"]
    if len(accepted) == len(accounts):
        request_state = "accepted"
    elif accepted:
        request_state = "partial"
    else:
        request_state = "rejected"

    draft_cleared = await _clear_launch_draft(
        engine,
        revision=body.draft_revision if accepted else None,
    )
    only = accounts[0] if len(accounts) == 1 else None
    return LaunchOut(
        run_id=only.run_id if only else None,
        task_id=only.task_id if only else None,
        status=only.status if only else request_state,
        idempotency_key=only.idempotency_key if only else None,
        draft_cleared=draft_cleared,
        request_state=request_state,
        accounts=accounts,
    )


# ─────────────────────────────── runs ────────────────────────────────


def _campaign_problem(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    correlation_id = str(getattr(request.state, "request_id", None) or uuid.uuid4())
    return JSONResponse(
        status_code=status_code,
        content=api_problem_payload(
            code=code,
            message=message,
            correlation_id=correlation_id,
        ),
        headers={"X-Request-Id": correlation_id},
    )


def _normalized_task_result(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


_CAMPAIGN_RUN_STATUSES = {
    "queued",
    "uniquifying",
    "uploading",
    "creating",
    "succeeded",
    "failed",
    "cancelled",
}
_INVALID_MEDIA_CONTROL_REASONS = {
    "invalid_media_checkpoint",
    "media_checkpoint_missing",
    "media_checkpoint_empty",
    "media_checkpoint_incomplete",
}


def _public_run_progress(*, run_status: str, value: object) -> RunProgressOut:
    """Project an arbitrary worker checkpoint into bounded operator facts."""

    checkpoint = value if isinstance(value, dict) else {}
    raw_stage = str(checkpoint.get("stage") or "")
    stage = raw_stage if raw_stage in _CAMPAIGN_RUN_STATUSES else run_status

    def non_negative_int(key: str) -> int | None:
        raw = checkpoint.get(key)
        return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else None

    # ``core.campaign_builder.execute._ProgressState.snapshot`` is the durable
    # worker checkpoint contract.  Keep its internal field names out of the API
    # while preserving the one stable, monotonic unit it exposes: completed ads.
    completed = non_negative_int("ads_done")
    total = non_negative_int("total_ads")
    if completed is not None and total is not None and completed > total:
        completed = None
        total = None
    return RunProgressOut(stage=stage, completed=completed, total=total)


def _campaign_run_failure_class(
    *,
    run_status: str,
    task_outcome: str | None,
    task_state: str | None,
    task_reason: str,
    external_started: bool,
    controls: CampaignRunControls,
) -> str | None:
    """Classify a terminal run without exposing raw worker diagnostics."""

    if run_status != "failed":
        return None
    resume = controls.resume
    resume_reason = str(resume.reason)
    if task_state is None or resume_reason == "campaign_task_missing":
        return "unavailable"
    if (
        task_outcome != "REJECTED"
        or task_state == "unknown"
        or external_started
        or resume_reason in {"external_boundary_crossed", "created_meta_objects_present"}
    ):
        return "manual_review"
    if task_reason == "invalid_config" or resume_reason == "invalid_config_checkpoint":
        return "invalid_config"
    if resume_reason in _INVALID_MEDIA_CONTROL_REASONS:
        return "invalid_media"
    if resume.available:
        return "safe_retry"
    return "unavailable"


@router.get("/tools/campaigns/runs", response_model=list[RunSummaryOut])
async def list_runs(
    engine: DepEngine,
    response: Response,
    status: str | None = Query(default=None, description="Фильтр по статусу run"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[RunSummaryOut]:
    """Список запусков (новые сверху). offer_code извлекается из снимка config."""
    conditions: list[str] = []
    params: dict = {"limit": limit, "offset": offset}
    if status:
        conditions.append("status = :status")
        params["status"] = status
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    query = f"""
        SELECT id::text AS id, preset_id::text AS preset_id, status,
               config->>'offer_code' AS offer_code,
               config#>>'{{account,act_id}}' AS account_id,
               idempotency_key,
               created_at::text AS created_at, updated_at::text AS updated_at
        FROM campaign_run
        {where}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """
    count_sql = f"SELECT COUNT(*) FROM campaign_run {where}"
    # LOW (аудит 02.07): count_sql не использует limit/offset — не передаём их лишними
    # bind-параметрами (SQLAlchemy их проглатывает молча, но это вводит в заблуждение
    # при чтении/отладке SQL).
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

    async with engine.connect() as conn:
        rows = (await conn.execute(text(query), params)).fetchall()
        total = (await conn.execute(text(count_sql), count_params)).scalar() or 0

    response.headers["X-Total-Count"] = str(total)
    return [RunSummaryOut(**dict(r._mapping)) for r in rows]


@router.get("/tools/campaigns/runs/{run_id}", response_model=RunDetailOut)
async def get_run(run_id: str, engine: DepEngine) -> RunDetailOut:
    """Return bounded progress, lifecycle evidence and safe control guidance."""
    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="run_id не UUID") from exc
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT run.id::text AS id,
                           run.preset_id::text AS preset_id,
                           run.status,
                           run.config,
                           run.progress,
                           run.created_meta_ids,
                           run.idempotency_key,
                           run.created_at::text AS created_at,
                           run.updated_at::text AS updated_at,
                           task.id AS task_id,
                           task.status AS task_status,
                           task.result AS task_result,
                           task.attempt_count,
                           task.max_attempts,
                           task.requested_by,
                           task.external_started_at,
                           task.cancel_requested_at,
                           task.deadline_at,
                           task.created_at AS task_created_at,
                           task.updated_at AS task_updated_at,
                           task.completed_at AS task_completed_at,
                           task.correlation_id
                    FROM campaign_run AS run
                    LEFT JOIN LATERAL (
                        SELECT id, status, result, attempt_count, max_attempts,
                               requested_by,
                               external_started_at, cancel_requested_at,
                               deadline_at, created_at, updated_at, completed_at,
                               correlation_id
                        FROM task_queue
                        WHERE task_type = 'campaign_create'
                          AND payload->>'run_id' = run.id::text
                        ORDER BY id DESC
                        LIMIT 1
                    ) AS task ON TRUE
                    WHERE run.id = :rid
                    LIMIT 1
                    """
                ),
                {"rid": rid},
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Запуск id={run_id} не найден")
    values = dict(row._mapping)
    public_progress = _public_run_progress(
        run_status=str(values["status"]),
        value=values.pop("progress"),
    )
    result = _normalized_task_result(values.pop("task_result"))
    task: RunTaskOut | None = None
    task_context: dict[str, object] | None = None
    task_id = values.pop("task_id")
    task_status = values.pop("task_status")
    attempt_count = values.pop("attempt_count")
    max_attempts = values.pop("max_attempts")
    requested_by = values.pop("requested_by")
    external_started_at = values.pop("external_started_at")
    cancel_requested_at = values.pop("cancel_requested_at")
    deadline_at = values.pop("deadline_at")
    task_created_at = values.pop("task_created_at")
    task_updated_at = values.pop("task_updated_at")
    task_completed_at = values.pop("task_completed_at")
    values.pop("correlation_id")
    task_state: str | None = None
    outcome: str | None = None
    task_reason = str(result.get("reason") or "")
    if task_id is not None and task_status is not None:
        raw_outcome = str(result.get("outcome") or "").upper()
        outcome = "UNKNOWN" if result.get("reconcile_required") is True else raw_outcome
        if outcome not in {"CONFIRMED", "REJECTED", "UNKNOWN"}:
            outcome = None
        task_state = campaign_task_state(status=str(task_status), result=result)
        task = RunTaskOut(
            id=int(task_id),
            state=task_state,
            queue_status=str(task_status),
            outcome=outcome,
            attempt_count=int(attempt_count or 0),
            max_attempts=int(max_attempts or 1),
            requested_by=str(requested_by or ""),
            external_started=external_started_at is not None,
            cancel_requested_at=cancel_requested_at,
            deadline_at=deadline_at,
            created_at=task_created_at,
            updated_at=task_updated_at,
            completed_at=task_completed_at,
        )
        task_context = {
            "task_status": str(task_status),
            "task_result": result,
            "external_started_at": external_started_at,
            "cancel_requested_at": cancel_requested_at,
        }
    controls = campaign_run_controls(
        run_status=str(values["status"]),
        run_config=values["config"] if isinstance(values["config"], dict) else {},
        created_meta_ids=(
            values["created_meta_ids"] if isinstance(values["created_meta_ids"], dict) else {}
        ),
        task=task_context,
    )
    return RunDetailOut(
        **values,
        progress=public_progress,
        failure_class=_campaign_run_failure_class(
            run_status=str(values["status"]),
            task_outcome=outcome,
            task_state=task_state,
            task_reason=task_reason,
            external_started=external_started_at is not None,
            controls=controls,
        ),
        task=task,
        controls=RunControlsOut(
            abort=RunControlOptionOut(
                available=controls.abort.available,
                reason=controls.abort.reason,
            ),
            resume=RunControlOptionOut(
                available=controls.resume.available,
                reason=controls.resume.reason,
            ),
        ),
    )


async def _run_control_command(
    *,
    action: Literal["abort", "resume"],
    run_id: uuid.UUID,
    engine: DepEngine,
    request: Request,
    response: Response,
    idempotency_key: str,
) -> RunCommandOut | JSONResponse:
    requested_by = str(getattr(request.state, "operator_principal", "operator:web"))
    try:
        scoped_idempotency_key = principal_scoped_idempotency_key(
            principal=requested_by,
            client_key=idempotency_key,
        )
        service = CommandService(engine)
        if action == "abort":
            receipt = await service.abort_campaign_run(
                run_id=run_id,
                requested_by=requested_by,
                idempotency_key=scoped_idempotency_key,
            )
        else:
            receipt = await service.resume_campaign_run(
                run_id=run_id,
                requested_by=requested_by,
                idempotency_key=scoped_idempotency_key,
            )
    except CampaignRunNotFoundError:
        return _campaign_problem(
            request,
            status_code=404,
            code="campaign_run_not_found",
            message="Запуск кампании не найден",
        )
    except CampaignRunIdempotencyConflictError:
        return _campaign_problem(
            request,
            status_code=409,
            code="idempotency_conflict",
            message="Idempotency-Key уже связан с другой командой",
        )
    except CampaignRunControlUnavailableError as exc:
        return _campaign_problem(
            request,
            status_code=409,
            code=exc.reason,
            message=f"Команда {action} недоступна: {exc.reason}",
        )
    except ValueError as exc:
        return _campaign_problem(
            request,
            status_code=422,
            code="invalid_campaign_run_command",
            message=str(exc),
        )

    response.status_code = (
        status.HTTP_202_ACCEPTED if receipt.state in {"queued", "running"} else status.HTTP_200_OK
    )
    return RunCommandOut(
        action=receipt.action,
        run_id=str(receipt.run_id),
        task_id=receipt.task_id,
        state=receipt.state,
        run_status=receipt.run_status,
        created=receipt.created,
        reason=receipt.reason,
    )


@router.post(
    "/tools/campaigns/runs/{run_id}/abort",
    response_model=RunCommandOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_RUN_COMMAND_PROBLEM_RESPONSES,
)
async def abort_run(
    run_id: uuid.UUID,
    engine: DepEngine,
    request: Request,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
    ),
) -> RunCommandOut | JSONResponse:
    """Request cooperative cancellation; acceptance is never completion."""
    return await _run_control_command(
        action="abort",
        run_id=run_id,
        engine=engine,
        request=request,
        response=response,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/tools/campaigns/runs/{run_id}/resume",
    response_model=RunCommandOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_RUN_COMMAND_PROBLEM_RESPONSES,
)
async def resume_run(
    run_id: uuid.UUID,
    engine: DepEngine,
    request: Request,
    response: Response,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
    ),
) -> RunCommandOut | JSONResponse:
    """Resume only a verified REJECTED pre-external checkpoint."""
    return await _run_control_command(
        action="resume",
        run_id=run_id,
        engine=engine,
        request=request,
        response=response,
        idempotency_key=idempotency_key,
    )
