# -*- coding: utf-8 -*-
"""Роутер сервиса создания FB-кампаний (campaigns_create).

Money-критично: создаёт записи, по которым воркер заливает кампании в Meta и
тратит рекламный бюджет. Защита — idempotency_key (offer+date+хеш структуры) +
кампания всегда PAUSED по launch_state (спенда нет до ручного снятия паузы).

Все маршруты под prefix /api (auto-discovery) → /api/tools/campaigns/*.
X-API-Key на write-методах ставит ApiKeyAuthMiddleware (без dev-tools gate).

Endpoints:
    GET/POST/PUT/DELETE /tools/campaigns/presets[/{id}]  — CRUD пресетов
    POST   /tools/campaigns/upload                       — загрузка концептов
    POST   /tools/campaigns/validate                     — dry-run план
    POST   /tools/campaigns/launch                       — создать run + задачу
    GET    /tools/campaigns/runs[/{id}]                  — список / детали
    POST   /tools/campaigns/runs/{id}/clone              — клон в черновик
    POST   /tools/campaigns/runs/{id}/cancel             — отмена в очереди
    POST   /tools/campaigns/runs/{id}/cleanup            — пометить на снос Meta
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import text

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.campaigns_create import (
    AdsetPlanOut,
    CampaignPlanOut,
    CleanupOut,
    LaunchIn,
    LaunchOut,
    PresetIn,
    PresetOut,
    RunDetailOut,
    RunSummaryOut,
    UploadConceptsOut,
    UploadedConceptOut,
    ValidateIn,
    ValidatePlanOut,
)
from core.campaign_builder.builder import build_campaign_spec, total_code_span
from core.campaign_builder.config import CampaignConfig, ref_media_kind
from core.campaign_builder.creative_ledger import allocate_code_span, peek_next_seq

logger = logging.getLogger(__name__)

router = APIRouter(tags=["campaigns"])

# task_type воркера-исполнителя (контракт со стримом campaign_creator_worker).
CAMPAIGN_TASK_TYPE = "campaign_create"

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


# Отмена разрешена ТОЛЬКО пока воркер не начал исполнение (queued). Как только воркер
# атомарно перевёл queued→uniquifying, cancel получает 409. Иначе была cancel-гонка: cancel
# при uniquifying/uploading ставил run=cancelled, но воркер (не перечитывая статус) всё равно
# создавал PAUSED-кампанию вопреки 200 на cancel → призрак. Спенда не было (PAUSED), но намерение нарушалось.
_CANCELLABLE_RUN_STATUSES = ("queued",)


def _campaign_upload_root() -> Path:
    """Корень временных папок загрузки концептов (per-run).

    Env CAMPAIGN_UPLOAD_ROOT переопределяет дефолт (на удалённом хосте — рядом с
    воркером). Дефолт — ~/Documents/FB_Agent_Campaign_Uploads.
    """
    raw = os.environ.get("CAMPAIGN_UPLOAD_ROOT", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / "Documents" / "FB_Agent_Campaign_Uploads"


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
    canonical = config.model_dump_json()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"campaign:{config.offer_code}:{config.start_date}:{digest}"


# ─────────────────────────────── presets ────────────────────────────────


_PRESET_COLUMNS = """
    id::text AS id, name, act_id, page_id, pixel_id, tz_offset,
    offer_code, byer_tag, objective, optimization_goal, custom_event_type,
    special_ad_categories, cta, text_optimizations,
    click_through_days, view_through_days, url_tags_template, naming_template,
    extra, created_at::text AS created_at, updated_at::text AS updated_at
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
    params["special_ad_categories"] = json.dumps(params["special_ad_categories"])
    params["extra"] = json.dumps(params["extra"])
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
                        (name, act_id, page_id, pixel_id, tz_offset, offer_code, byer_tag,
                         objective, optimization_goal, custom_event_type,
                         special_ad_categories, cta, text_optimizations,
                         click_through_days, view_through_days,
                         url_tags_template, naming_template, extra)
                    VALUES
                        (:name, :act_id, :page_id, :pixel_id, :tz_offset, :offer_code, :byer_tag,
                         :objective, :optimization_goal, :custom_event_type,
                         CAST(:special_ad_categories AS JSONB), :cta, :text_optimizations,
                         :click_through_days, :view_through_days,
                         :url_tags_template, :naming_template, CAST(:extra AS JSONB))
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
    params["special_ad_categories"] = json.dumps(params["special_ad_categories"])
    params["extra"] = json.dumps(params["extra"])
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
                        name=:name, act_id=:act_id, page_id=:page_id, pixel_id=:pixel_id,
                        tz_offset=:tz_offset, offer_code=:offer_code, byer_tag=:byer_tag,
                        objective=:objective, optimization_goal=:optimization_goal,
                        custom_event_type=:custom_event_type,
                        special_ad_categories=CAST(:special_ad_categories AS JSONB),
                        cta=:cta, text_optimizations=:text_optimizations,
                        click_through_days=:click_through_days, view_through_days=:view_through_days,
                        url_tags_template=:url_tags_template, naming_template=:naming_template,
                        extra=CAST(:extra AS JSONB), updated_at=NOW()
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
async def upload_concepts(files: list[UploadFile] = File(...)) -> UploadConceptsOut:
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

    upload_id = uuid.uuid4().hex
    upload_dir = _campaign_upload_root() / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Любая ошибка валидации/размера → сносим всю temp-папку (нет утечки частичных файлов).
    try:
        concepts = await _stream_uploads_to_dir(files, upload_dir)
    except HTTPException:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise

    total_bytes = sum(c.size_bytes for c in concepts)
    return UploadConceptsOut(
        upload_id=upload_id,
        upload_dir=str(upload_dir),
        concepts=concepts,
        total_bytes=total_bytes,
    )


async def _stream_uploads_to_dir(
    files: list[UploadFile], upload_dir: Path
) -> list[UploadedConceptOut]:
    """Стримит каждый файл по чанкам на диск с cap-check и magic-валидацией типа.

    Money/OOM-инварианты:
    - суммарный размер проверяется ПО ХОДУ чтения (не читаем весь файл в RAM до cap-check);
    - первый чанк сниффится: расширение vs содержимое (переименованный файл → 422 ДО
      создания объектов в Meta, иначе орфаны после падения уникализатора).
    """
    concepts: list[UploadedConceptOut] = []
    total_bytes = 0
    seen: set[str] = set()
    for index, upload in enumerate(files):
        fname = _safe_filename(upload.filename or "", index)
        # Гарантируем уникальность имени внутри папки (коллизии после санитизации).
        if fname in seen:
            fname = f"{Path(fname).stem}_{index}{Path(fname).suffix}"
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

    concept_counts (число концептов на блок) передаётся в build_campaign_spec —
    раскладка K концептов × copies (сквозная нумерация ads), как у исполнителя.
    """
    try:
        config = body.domain_config()
        # Превью показывает реалистичные коды: продолжаем нумерацию оффера.
        # peek_next_seq — read-only, счётчик не двигает.
        async with engine.connect() as conn:
            config.code_start = await peek_next_seq(conn, config.offer_code) + 1
        spec = build_campaign_spec(config, concept_counts=body.concept_counts_map())
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"Невалидный конфиг: {exc}") from exc

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
        launch_state=spec.launch_state.value,
        copies_per_concept=spec.copies_per_concept,
        campaign_count=len(campaigns),
        adset_count=total_adsets,
        ad_count=total_ads,
        campaigns=campaigns,
    )


# ─────────────────────────────── launch ────────────────────────────────


@router.post("/tools/campaigns/launch", response_model=LaunchOut, status_code=201)
async def launch_campaign(body: LaunchIn, engine: DepEngine) -> LaunchOut:
    """Создать campaign_run(queued) + task_queue(campaign_create) в одной транзакции.

    Money-safety: idempotency_key (по конфигу) общий для run и задачи. Повторный
    launch того же конфига → находим существующий run, ничего не дублируем (200-shape).
    Воркер по run_id грузит CampaignRun и исполняет залив.
    """
    # Нормализуем плоский/вложенный вход в доменный CampaignConfig (единая точка).
    config = body.domain_config()
    # Валидируем структуру через builder (тот же путь и та же раскладка K, что validate):
    # concept_counts → превью, по которому байер апрувил, и залив сверяются на одной спеке.
    counts = body.concept_counts_map()
    try:
        build_campaign_spec(config, concept_counts=counts)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=f"Невалидный конфиг: {exc}") from exc

    # Fail-fast: блок без концептов создаст обречённый run (воркер упадёт на resolve_concepts).
    # build_campaign_spec этого не ловит (0 концептов = 0 ads, не ошибка). Отбиваем ДО создания
    # run/задачи — чтобы в истории не плодились заведомо-failed заливы (UX + чистота очереди).
    if counts is not None:
        empty = sorted(k for k, v in counts.items() if v < 1)
        if empty:
            raise HTTPException(
                status_code=422,
                detail=f"Кампании без концептов: {', '.join(empty)} — назначь хотя бы один концепт",
            )

    ikey = body.idempotency_key or _compute_idempotency_key(config)
    # В БД пишем КАНОНИЧЕСКИЙ доменный снимок (воркер ждёт вложенный CampaignConfig).
    config_json = config.model_dump_json()

    preset_uuid: uuid.UUID | None = None
    if body.preset_id:
        try:
            preset_uuid = uuid.UUID(body.preset_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="preset_id не UUID") from exc

    async with engine.begin() as conn:
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
            return LaunchOut(
                run_id=existing.id,
                task_id=int(task_row.id) if task_row else None,
                status=existing.status,
                idempotency_key=ikey,
            )

        run_id = run_row.id

        # Per-offer нумерация: резервируем диапазон кодов ТОЛЬКО для реально нового
        # run'а (на конфликт-ветке аллокации нет → без gap'ов на повторах). code_start
        # фиксируется в config → воркер и retry берут одни и те же коды (preview==launch).
        span = total_code_span(config)
        base = await allocate_code_span(conn, config.offer_code, span)
        await conn.execute(
            text(
                "UPDATE campaign_run SET config = jsonb_set(config, '{code_start}', "
                "to_jsonb(:base)) WHERE id = CAST(:rid AS UUID)"
            ),
            {"base": base, "rid": run_id},
        )

        # Задача воркера: payload = {run_id}. task_type='campaign_create' (контракт).
        # Тот же idempotency_key — двойная защита от дубля залива. ON CONFLICT DO NOTHING:
        # если задача уже есть (гонка/повтор) — берём существующую, не плодим дубль.
        task_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload,
                         attempt_count, max_attempts, requested_by)
                    VALUES
                        (:tt, 'pending', :ik, CAST(:pl AS JSONB), 0, 5, :rb)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "tt": CAMPAIGN_TASK_TYPE,
                    "ik": ikey,
                    "pl": json.dumps({"run_id": run_id}),
                    "rb": "api_launch",
                },
            )
        ).first()
        if task_row is None:
            task_row = (
                await conn.execute(
                    text(
                        "SELECT id FROM task_queue WHERE idempotency_key = :ik "
                        "AND task_type = :tt LIMIT 1"
                    ),
                    {"ik": ikey, "tt": CAMPAIGN_TASK_TYPE},
                )
            ).first()

    task_id = int(task_row.id) if task_row else None
    logger.info("campaign launch: run_id=%s task_id=%s ikey=%s", run_id, task_id, ikey)
    return LaunchOut(
        run_id=run_id,
        task_id=task_id,
        status="queued",
        idempotency_key=ikey,
    )


# ─────────────────────────────── runs ────────────────────────────────


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
               idempotency_key, error,
               created_at::text AS created_at, updated_at::text AS updated_at
        FROM campaign_run
        {where}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """
    count_sql = f"SELECT COUNT(*) FROM campaign_run {where}"

    async with engine.connect() as conn:
        rows = (await conn.execute(text(query), params)).fetchall()
        total = (await conn.execute(text(count_sql), params)).scalar() or 0

    response.headers["X-Total-Count"] = str(total)
    return [RunSummaryOut(**dict(r._mapping)) for r in rows]


@router.get("/tools/campaigns/runs/{run_id}", response_model=RunDetailOut)
async def get_run(run_id: str, engine: DepEngine) -> RunDetailOut:
    """Детали запуска: конфиг-снимок, прогресс, созданные Meta-ID, ошибка."""
    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="run_id не UUID") from exc
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id::text AS id, preset_id::text AS preset_id, status,
                           config, progress, created_meta_ids, error, idempotency_key,
                           created_at::text AS created_at, updated_at::text AS updated_at
                    FROM campaign_run WHERE id = :rid LIMIT 1
                    """
                ),
                {"rid": rid},
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Запуск id={run_id} не найден")
    return RunDetailOut(**dict(row._mapping))


@router.post("/tools/campaigns/runs/{run_id}/clone", response_model=LaunchOut, status_code=201)
async def clone_run(run_id: str, engine: DepEngine) -> LaunchOut:
    """Клон запуска в новый черновик-run (status=queued БЕЗ задачи).

    Клон не заливает сразу: создаёт queued-run с новым idempotency_key
    (config + другой start_date/повторный залив должны иметь свой ключ).
    Пользователь правит и жмёт launch отдельно. Здесь — только заготовка config.
    """
    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="run_id не UUID") from exc

    async with engine.begin() as conn:
        src = (
            await conn.execute(
                text("SELECT config, preset_id FROM campaign_run WHERE id = :rid LIMIT 1"),
                {"rid": rid},
            )
        ).first()
        if src is None:
            raise HTTPException(status_code=404, detail=f"Запуск id={run_id} не найден")

        # Новый клон-черновик: idempotency_key=NULL (ещё не залив, задачи нет).
        config_json = src.config if isinstance(src.config, str) else json.dumps(src.config)
        clone_row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO campaign_run (preset_id, config, status, idempotency_key)
                    VALUES (:preset_id, CAST(:config AS JSONB), 'queued', NULL)
                    RETURNING id::text AS id, status
                    """
                ),
                {"preset_id": src.preset_id, "config": config_json},
            )
        ).first()

    return LaunchOut(
        run_id=clone_row.id,
        task_id=None,
        status=clone_row.status,
        idempotency_key="",
    )


@router.post("/tools/campaigns/runs/{run_id}/cancel", response_model=RunSummaryOut)
async def cancel_run(run_id: str, engine: DepEngine) -> RunSummaryOut:
    """Отмена запуска в очереди (status=cancelled + отмена задачи).

    Money-гард: отмена только ДО creating (необратимое создание Meta уже идёт).
    409 если run уже creating/succeeded/failed/cancelled.
    """
    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="run_id не UUID") from exc

    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT status, idempotency_key FROM campaign_run WHERE id = :rid LIMIT 1"),
                {"rid": rid},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Запуск id={run_id} не найден")
        if row.status not in _CANCELLABLE_RUN_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Нельзя отменить запуск в статусе '{row.status}' — "
                    f"отмена только до начала создания (creating)"
                ),
            )

        updated = (
            await conn.execute(
                text(
                    """
                    UPDATE campaign_run
                    SET status = 'cancelled', updated_at = NOW()
                    WHERE id = :rid AND status = ANY(:allowed)
                    RETURNING id::text AS id, preset_id::text AS preset_id, status,
                              config->>'offer_code' AS offer_code, idempotency_key, error,
                              created_at::text AS created_at, updated_at::text AS updated_at
                    """
                ),
                {"rid": rid, "allowed": list(_CANCELLABLE_RUN_STATUSES)},
            )
        ).first()
        # rowcount=0 → статус сменился между SELECT и UPDATE (гонка с воркером).
        if updated is None:
            raise HTTPException(
                status_code=409, detail="Состояние запуска изменилось — повторите запрос"
            )

        # Отменяем связанную задачу, если ещё не исполняется.
        if row.idempotency_key:
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'cancelled', completed_at = NOW(), updated_at = NOW()
                    WHERE idempotency_key = :ik
                      AND task_type = :tt
                      AND status IN ('draft', 'pending', 'retrying')
                    """
                ),
                {"ik": row.idempotency_key, "tt": CAMPAIGN_TASK_TYPE},
            )

    return RunSummaryOut(**dict(updated._mapping))


@router.post("/tools/campaigns/runs/{run_id}/cleanup", response_model=CleanupOut)
async def cleanup_run(run_id: str, engine: DepEngine) -> CleanupOut:
    """Пометить созданные Meta-объекты на снос (partial-fail recovery).

    Возвращает created_meta_ids run для ручного/задачного сноса. Реальный снос —
    отдельной meta-мутацией (вне scope этого роутера). Money-safe: только читает
    список id, ничего сам в Meta не трогает.
    """
    try:
        rid = uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="run_id не UUID") from exc
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT created_meta_ids FROM campaign_run WHERE id = :rid LIMIT 1"),
                {"rid": rid},
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Запуск id={run_id} не найден")

    meta_ids = row.created_meta_ids
    if isinstance(meta_ids, str):
        meta_ids = json.loads(meta_ids)
    meta_ids = meta_ids or {}

    if not meta_ids:
        detail = "Созданных Meta-объектов нет — снос не требуется"
    else:
        detail = "Список созданных Meta-объектов для сноса (реальный снос — отдельной мутацией)"
    return CleanupOut(run_id=run_id, meta_ids=meta_ids, detail=detail)
