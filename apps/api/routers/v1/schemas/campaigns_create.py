# -*- coding: utf-8 -*-
"""Pydantic-схемы роутера campaigns_create (сервис создания FB-кампаний).

Контракт API↔воркер. CampaignConfig переиспользуется из core.campaign_builder
без форка — единый источник правды по структуре конфига залива.

КОНТРАКТ ФРОНТ↔БЭК (CRIT-2). Web шлёт единственный плоский конфиг
(`act_id`/`daily_budget`/`countries`/… на верхнем уровне).
`CampaignConfigIn` в ОДНОМ месте (`to_domain`) конвертирует его во внутренний
`CampaignConfig`. Внутренняя вложенная модель никогда не является публичным API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from core.campaign_builder.config import (
    Account,
    AdsetConfig,
    AdText,
    Attribution,
    Budget,
    CampaignBlock,
    CampaignConfig,
    Targeting,
)

# ────────────────────────────── flat config (контракт фронта) ──────────────────────────────


class CampaignStructureIn(BaseModel):
    """Одна кампания в плоской структуре фронта.

    Несёт `key`/`label`/`adset_count`/`concept_refs`. Доменные имена adset'ов
    и dir/glob генерируются детерминированно в `to_domain` (фронт их не знает).
    `label` — произвольная метка кампании; если задана, добавляется в конец имени
    кампании и каждого adset'а. Пустая/None — ничего не добавляется.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    label: str | None = None  # произвольная метка кампании (в конец имени)
    adset_count: int = Field(ge=1)
    concept_refs: list[str] = Field(min_length=1)


class AdTextIn(BaseModel):
    """Текст объявления в форме фронта (mode none|text, primary)."""

    model_config = ConfigDict(extra="forbid")

    mode: str = "none"  # none | text (фронт) → none | full (домен)
    primary: str = ""


class CampaignConfigIn(BaseModel):
    """Плоский конфиг залива от фронта. `to_domain()` → доменный CampaignConfig.

    Все money-инварианты (hard-cap бюджета, +AQ, дефолты) проверяет уже доменный
    CampaignConfig внутри `to_domain` — здесь сознательно НЕ дублируем валидацию,
    чтобы единый источник правды остался в core.campaign_builder.config.
    """

    model_config = ConfigDict(extra="forbid")

    # account
    act_id: str
    page_id: str
    pixel_id: str

    # идентичность залива
    offer_code: str
    byer_tag: str | None = None
    objective: str = "OUTCOME_SALES"
    optimization_goal: str = "OFFSITE_CONVERSIONS"
    custom_event_type: str = "PURCHASE"
    special_ad_categories: list[str] = Field(default_factory=lambda: ["NONE"])
    destination_link: str
    cta: str = "PLAY_GAME"
    text_optimizations: str = "OPT_OUT"
    start_date: str | None = None

    # текст
    ad_text: AdTextIn | None = None

    # бюджет
    budget_level: str = "campaign"
    daily_budget: str = Field(
        strict=True,
        pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
        max_length=32,
    )
    bid_strategy: str = "COST_CAP"  # SOP: реальные кампании кабинета всегда COST_CAP
    # Major-unit decimal string. Currency and exponent come only from durable
    # Meta account evidence; the client cannot provide or override either.
    bid_amount: str | None = Field(
        default=None,
        strict=True,
        pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
        max_length=32,
    )

    # таргет
    countries: list[str] = Field(default_factory=list)
    age_min: int = 21
    age_max: int = 65
    advantage_audience: bool = True

    # атрибуция
    click_through_days: int = 1
    view_through_days: int = 1

    # структура / креативы
    campaigns: list[CampaignStructureIn] = Field(min_length=1)
    copies_per_concept: int | None = None
    creo_root: str = Field(min_length=1)

    # Опциональный custom template. Domain builder в любом случае добавит
    # `sub8={{ad.id}}`, если его нет.
    url_tags: str | None = None
    naming_template: str | None = None

    def _ad_text_domain(self) -> AdText:
        """Конверсия текста: фронтовый mode 'text' → доменный 'full'."""
        if self.ad_text is None or self.ad_text.mode == "none":
            return AdText(mode="none")
        return AdText(mode="full", message=self.ad_text.primary)

    def _campaign_blocks(self) -> list[CampaignBlock]:
        """Разворачивает плоские кампании в доменные CampaignBlock с adset'ами.

        Имена кампаний/adset'ов — детерминированные шаблоны SOP
        (`{byer} | {offer} | adset.pro | {date}`). Если задана `label` кампании —
        добавляется в конец имени кампании и каждого adset'а (`| label`).
        dir/glob для adset'а в build_campaign_spec не используются (только имена/счётчик) —
        ставим стабильные плейсхолдеры из key концепта.
        """
        blocks: list[CampaignBlock] = []
        for camp in self.campaigns:
            user_label = (camp.label or "").strip()
            suffix = f" | {user_label}" if user_label else ""
            adsets = [
                AdsetConfig(
                    name=f"{{byer}} | {{offer}} | s{i} | {{date}}{suffix}",
                    dir=f"{camp.key}/a{i}",
                    glob="*",
                )
                for i in range(1, camp.adset_count + 1)
            ]
            blocks.append(
                CampaignBlock(
                    key=camp.key,
                    name=f"{{byer}} | {{offer}} | adset.pro | {{date}}{suffix}",
                    adsets=adsets,
                    # ЕДИНЫЙ источник концептов: имена файлов из upload-ответа, назначенные
                    # фронтом на эту кампанию. Воркер резолвит {creo_root}/{ref} по каждому —
                    # без рассинхрона с validate (len concept_refs).
                    concept_refs=list(camp.concept_refs),
                )
            )
        return blocks

    def to_domain(
        self,
        *,
        timezone_name: str,
        currency: str,
        account_context_observed_at: datetime,
        now: datetime | None = None,
    ) -> CampaignConfig:
        """Единая точка конвертации плоского входа в доменный CampaignConfig.

        Timezone/currency evidence is supplied only by the server-side durable
        account-context resolver.  ``start_date`` defaults to the next cabinet
        local day and is validated against that same IANA timezone.
        """
        observed_now = now or datetime.now(UTC)
        if observed_now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        local_today = observed_now.astimezone(ZoneInfo(timezone_name)).date()
        start_date = self.start_date or (local_today + timedelta(days=1)).isoformat()
        try:
            parsed_start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("start_date must be a real YYYY-MM-DD date") from exc
        if parsed_start_date <= local_today:
            raise ValueError("start_date must be later than the current cabinet-local day")

        fields: dict[str, Any] = {
            "account": Account(
                act_id=self.act_id,
                page_id=self.page_id,
                pixel_id=self.pixel_id,
                timezone_name=timezone_name,
                currency=currency,
                account_context_observed_at=account_context_observed_at,
            ),
            "offer_code": self.offer_code,
            "objective": self.objective,
            "optimization_goal": self.optimization_goal,
            "custom_event_type": self.custom_event_type,
            "special_ad_categories": self.special_ad_categories,
            "destination_link": self.destination_link,
            "url_tags_template": self.url_tags,
            "cta": self.cta,
            "text_optimizations": self.text_optimizations,
            "budget": Budget(
                level=self.budget_level,
                currency=currency,
                daily_amount=self.daily_budget,
                bid_strategy=self.bid_strategy,
                bid_amount=self.bid_amount,
            ),
            "targeting": Targeting(
                countries=self.countries,
                age_min=self.age_min,
                age_max=self.age_max,
                advantage_audience=self.advantage_audience,
            ),
            "attribution": Attribution(
                click_through_days=self.click_through_days,
                view_through_days=self.view_through_days,
            ),
            "ad_text": self._ad_text_domain(),
            "campaigns": self._campaign_blocks(),
            "creo_root": self.creo_root,
            "start_date": parsed_start_date.isoformat(),
        }
        # byer_tag/copies_per_concept опциональны — ставим только при наличии.
        if self.byer_tag:
            fields["byer_tag"] = self.byer_tag
        if self.copies_per_concept is not None:
            fields["copies_per_concept"] = self.copies_per_concept
        return CampaignConfig(**fields)


# ────────────────────────────── presets ──────────────────────────────


class PresetIn(BaseModel):
    """Тело создания/обновления пресета (стабильный конфиг залива)."""

    name: str = Field(min_length=1, max_length=255)
    act_id: str = Field(min_length=1, max_length=64)
    page_id: str = Field(min_length=1, max_length=64)
    pixel_id: str = Field(min_length=1, max_length=64)
    offer_code: str | None = Field(default=None, max_length=64)
    byer_tag: str | None = Field(default=None, max_length=64)
    objective: str = "OUTCOME_SALES"
    optimization_goal: str = "OFFSITE_CONVERSIONS"
    custom_event_type: str = "PURCHASE"
    special_ad_categories: list[str] = Field(default_factory=lambda: ["NONE"])
    cta: str = "PLAY_GAME"
    text_optimizations: str = "OPT_OUT"
    click_through_days: int = 1
    view_through_days: int = 1
    url_tags_template: str | None = Field(default=None, max_length=1024)
    naming_template: str | None = Field(default=None, max_length=512)
    extra: dict[str, Any] = Field(default_factory=dict)


class PresetOut(BaseModel):
    """Пресет в ответе API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    act_id: str
    page_id: str
    pixel_id: str
    offer_code: str | None
    byer_tag: str | None
    objective: str
    optimization_goal: str
    custom_event_type: str
    special_ad_categories: list[str]
    cta: str
    text_optimizations: str
    click_through_days: int
    view_through_days: int
    url_tags_template: str | None
    naming_template: str | None
    extra: dict[str, Any]
    created_at: str
    updated_at: str


# ────────────────────────────── upload ──────────────────────────────


class UploadedConceptOut(BaseModel):
    """Метаданные одного загруженного концепта (превью для UI)."""

    ref: str  # относительный путь внутри run-папки (имя файла)
    original_name: str
    size_bytes: int
    content_type: str | None = None


class UploadConceptsOut(BaseModel):
    """Ответ загрузки концептов: id временной папки + список файлов."""

    upload_id: str  # uuid временной папки (вход в config.creo_root воркера)
    upload_dir: str  # абсолютный путь к папке на сервере
    concepts: list[UploadedConceptOut]
    added_refs: list[str]  # refs, добавленные именно этим запросом
    total_bytes: int


# ────────────────────────────── validate ──────────────────────────────


class ValidateIn(BaseModel):
    """Запрос dry-run валидации конфига.

    `config` — единственная каноническая плоская форма фронта (`CampaignConfigIn`).
    """

    model_config = ConfigDict(extra="forbid")

    config: CampaignConfigIn

    def domain_config(
        self,
        *,
        timezone_name: str,
        currency: str,
        account_context_observed_at: datetime,
        now: datetime | None = None,
    ) -> CampaignConfig:
        """Convert the public flat contract to the internal domain model."""
        return self.config.to_domain(
            timezone_name=timezone_name,
            currency=currency,
            account_context_observed_at=account_context_observed_at,
            now=now,
        )


class AdsetPlanOut(BaseModel):
    """Сводка по одному adset в плане."""

    name: str
    status: Literal["PAUSED"]
    ad_count: int


class CampaignPlanOut(BaseModel):
    """Сводка по одной кампании в плане."""

    key: str
    name: str
    status: Literal["PAUSED"]
    adsets: list[AdsetPlanOut]


class ValidatePlanOut(BaseModel):
    """Результат validate: число объектов + нейминг без создания."""

    offer_code: str
    creation_policy: Literal["all_paused"]
    copies_per_concept: int
    campaign_count: int
    adset_count: int
    ad_count: int
    campaigns: list[CampaignPlanOut]
    start_date: str
    start_time: str
    timezone_name: str
    currency: str
    account_context_observed_at: datetime


# ────────────────────────────── launch ──────────────────────────────


class LaunchIn(BaseModel):
    """Запрос запуска залива: конфиг + опц. ссылка на пресет/upload.

    `config` — единственная каноническая плоская форма фронта (`CampaignConfigIn`).
    """

    model_config = ConfigDict(extra="forbid")

    config: CampaignConfigIn
    preset_id: str | None = None

    def domain_config(
        self,
        *,
        timezone_name: str,
        currency: str,
        account_context_observed_at: datetime,
        now: datetime | None = None,
    ) -> CampaignConfig:
        """Convert the public flat contract to the internal domain model."""
        return self.config.to_domain(
            timezone_name=timezone_name,
            currency=currency,
            account_context_observed_at=account_context_observed_at,
            now=now,
        )


class LaunchOut(BaseModel):
    """Ответ запуска: id созданного run + id задачи."""

    run_id: str
    task_id: int | None
    status: str
    idempotency_key: str


# ────────────────────────────── runs ──────────────────────────────

CampaignRunStatus = Literal[
    "queued",
    "uniquifying",
    "uploading",
    "creating",
    "succeeded",
    "failed",
    "cancelled",
]
CampaignTaskQueueStatus = Literal[
    "pending",
    "retrying",
    "running",
    "succeeded",
    "failed",
    "cancelled",
]
CampaignActionState = Literal[
    "queued",
    "running",
    "confirmed",
    "failed",
    "cancelled",
    "unknown",
]
CampaignRunFailureClass = Literal[
    "manual_review",
    "safe_retry",
    "invalid_config",
    "invalid_media",
    "unavailable",
]


class RunSummaryOut(BaseModel):
    """Краткая карточка запуска для списка."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    preset_id: str | None
    status: CampaignRunStatus
    offer_code: str | None
    idempotency_key: str | None
    created_at: str
    updated_at: str


class RunProgressOut(BaseModel):
    """Bounded operator progress; arbitrary worker checkpoint keys stay private."""

    stage: CampaignRunStatus
    completed: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)


class RunTaskOut(BaseModel):
    """Latest durable campaign task and its authoritative action lifecycle."""

    id: int
    state: CampaignActionState
    queue_status: CampaignTaskQueueStatus
    outcome: Literal["CONFIRMED", "REJECTED", "UNKNOWN"] | None
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    requested_by: str
    external_started: bool
    cancel_requested_at: datetime | None
    deadline_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class RunControlOptionOut(BaseModel):
    """One control and a stable machine-readable availability reason."""

    available: bool
    reason: str


class RunControlsOut(BaseModel):
    abort: RunControlOptionOut
    resume: RunControlOptionOut


class RunDetailOut(BaseModel):
    """Details plus the latest task and safe control availability."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    preset_id: str | None
    status: CampaignRunStatus
    config: dict[str, Any]
    progress: RunProgressOut
    created_meta_ids: dict[str, Any]
    failure_class: CampaignRunFailureClass | None
    idempotency_key: str | None
    created_at: str
    updated_at: str
    task: RunTaskOut | None
    controls: RunControlsOut


class RunCommandOut(BaseModel):
    """Accepted or replayed abort/resume command lifecycle."""

    action: Literal["abort", "resume"]
    run_id: str
    task_id: int
    state: CampaignActionState
    run_status: CampaignRunStatus
    created: bool
    reason: str
