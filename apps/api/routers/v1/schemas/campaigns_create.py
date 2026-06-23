# -*- coding: utf-8 -*-
"""Pydantic-схемы роутера campaigns_create (сервис создания FB-кампаний).

Контракт API↔воркер. CampaignConfig переиспользуется из core.campaign_builder
без форка — единый источник правды по структуре конфига залива.

КОНТРАКТ ФРОНТ↔БЭК (CRIT-2). Фронты (web `campaignWizard.buildConfig`, mini-визард)
шлют ПЛОСКИЙ конфиг (`act_id`/`daily_budget_cents`/`countries`/… на верхнем уровне).
Доменный CampaignConfig — ВЛОЖЕННЫЙ (`account`/`budget`/`targeting`/`campaigns[].adsets`).
`CampaignConfigIn` принимает плоскую форму фронта и в ОДНОМ месте (`to_domain`)
конвертирует её в доменный CampaignConfig. Вложенную (legacy) форму тоже принимаем
без конвертации — обратная совместимость со старыми клиентами/тестами.
"""

from __future__ import annotations

from typing import Any

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


def _kind_label(kind: str) -> str:
    """Лейбл типа медиа для нейминга (image→static, video→video)."""
    return "static" if kind == "image" else "video"


def _tz_offset_to_str(tz_offset: int | str | None) -> str:
    """Часовой сдвиг кабинета int (часы) → ISO `±HH:00` для start_time.

    Фронт шлёт целое число часов (напр. 0 или -7). Доменный Account ждёт строку
    `-07:00`. Если уже строка — отдаём как есть (legacy). None → дефолт Account.
    """
    if tz_offset is None:
        return "-07:00"
    if isinstance(tz_offset, str):
        return tz_offset
    sign = "-" if tz_offset < 0 else "+"
    return f"{sign}{abs(int(tz_offset)):02d}:00"


class CampaignStructureIn(BaseModel):
    """Одна кампания в плоской структуре фронта.

    Несёт только `key`/`kind`/`adset_count`/`concept_refs`. Доменные имена adset'ов
    и dir/glob генерируются детерминированно в `to_domain` (фронт их не знает).
    """

    key: str
    kind: str  # image | video
    adset_count: int = Field(ge=1)
    concept_refs: list[str] = Field(default_factory=list)


class AdTextIn(BaseModel):
    """Текст объявления в форме фронта (mode none|text, primary)."""

    model_config = ConfigDict(extra="ignore")

    mode: str = "none"  # none | text (фронт) → none | full (домен)
    primary: str = ""


class CampaignConfigIn(BaseModel):
    """Плоский конфиг залива от фронта. `to_domain()` → доменный CampaignConfig.

    Все money-инварианты (hard-cap бюджета, +AQ, дефолты) проверяет уже доменный
    CampaignConfig внутри `to_domain` — здесь сознательно НЕ дублируем валидацию,
    чтобы единый источник правды остался в core.campaign_builder.config.
    """

    model_config = ConfigDict(extra="ignore")

    # account
    act_id: str
    page_id: str
    pixel_id: str
    tz_offset: int | str | None = 0

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
    daily_budget_cents: int
    bid_strategy: str = "LOWEST_COST_WITHOUT_CAP"

    # таргет
    countries: list[str] = Field(default_factory=list)
    age_min: int = 18
    age_max: int = 65
    advantage_audience: bool = True

    # атрибуция
    click_through_days: int = 1
    view_through_days: int = 1

    # структура / креативы
    campaigns: list[CampaignStructureIn]
    copies_per_concept: int | None = None
    creo_root: str | None = None
    launch_state: str = "campaign_paused"

    # принимается, но в доменном CampaignConfig поля нет — игнорируется (extra=ignore
    # ловит его на верхнем уровне; объявляем явно для документации контракта).
    url_tags: str | None = None
    naming_template: str | None = None

    def concept_counts(self) -> dict[str, int]:
        """Число концептов на блок (ключ=block.key) для build_campaign_spec.

        Источник — длина concept_refs каждой кампании. При пустом списке — 0
        (build_campaign_spec падает раскладкой при необходимости, money-safe).
        """
        return {c.key: len(c.concept_refs) for c in self.campaigns}

    def _ad_text_domain(self) -> AdText:
        """Конверсия текста: фронтовый mode 'text' → доменный 'full'."""
        if self.ad_text is None or self.ad_text.mode == "none":
            return AdText(mode="none")
        return AdText(mode="full", message=self.ad_text.primary)

    def _campaign_blocks(self) -> list[CampaignBlock]:
        """Разворачивает плоские кампании в доменные CampaignBlock с adset'ами.

        Имена кампаний/adset'ов — детерминированные шаблоны SOP
        (`{byer} | {offer} | <type> | adset.pro | {date}`). dir/glob для adset'а
        в build_campaign_spec не используются (только имена/счётчик) — ставим
        стабильные плейсхолдеры из key концепта.
        """
        blocks: list[CampaignBlock] = []
        for camp in self.campaigns:
            label = _kind_label(camp.kind)
            adsets = [
                AdsetConfig(
                    name=f"{{byer}} | {{offer}} | {label} | s{i} | {{date}}",
                    dir=f"{camp.key}/a{i}",
                    glob="*",
                )
                for i in range(1, camp.adset_count + 1)
            ]
            blocks.append(
                CampaignBlock(
                    key=camp.key,
                    name=f"{{byer}} | {{offer}} | {label} | adset.pro | {{date}}",
                    kind=camp.kind,
                    adsets=adsets,
                    # ЕДИНЫЙ источник концептов: имена файлов из upload-ответа, назначенные
                    # фронтом на эту кампанию. Воркер резолвит {creo_root}/{ref} по каждому —
                    # без рассинхрона с validate (len concept_refs).
                    concept_refs=list(camp.concept_refs),
                )
            )
        return blocks

    def to_domain(self) -> CampaignConfig:
        """Единая точка конвертации плоского входа в доменный CampaignConfig.

        Доменная модель досверяет money-инварианты (hard-cap бюджета, формат даты,
        +AQ) — отсюда 422 на невалидный бюджет/конфиг прилетает из CampaignConfig.
        """
        fields: dict[str, Any] = {
            "account": Account(
                act_id=self.act_id,
                page_id=self.page_id,
                pixel_id=self.pixel_id,
                tz_offset=_tz_offset_to_str(self.tz_offset),
            ),
            "offer_code": self.offer_code,
            "objective": self.objective,
            "optimization_goal": self.optimization_goal,
            "custom_event_type": self.custom_event_type,
            "special_ad_categories": self.special_ad_categories,
            "destination_link": self.destination_link,
            "cta": self.cta,
            "text_optimizations": self.text_optimizations,
            "budget": Budget(
                level=self.budget_level,
                daily_cents=self.daily_budget_cents,
                bid_strategy=self.bid_strategy,
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
            "creo_root": self.creo_root or "",
            "launch_state": self.launch_state,
        }
        # byer_tag/start_date/copies_per_concept опциональны — ставим только при наличии,
        # иначе доменные дефолты (byer='MV', start_date=today+1).
        if self.byer_tag:
            fields["byer_tag"] = self.byer_tag
        if self.start_date:
            fields["start_date"] = self.start_date
        if self.copies_per_concept is not None:
            fields["copies_per_concept"] = self.copies_per_concept
        return CampaignConfig(**fields)


def _coerce_to_domain(value: Any) -> CampaignConfig:
    """Нормализует вход config в доменный CampaignConfig.

    Плоская форма фронта (`act_id`/`countries` на верхнем уровне) → через
    CampaignConfigIn.to_domain. Вложенная (legacy: `account`/`targeting`) —
    напрямую в CampaignConfig (обратная совместимость).
    """
    if isinstance(value, CampaignConfig):
        return value
    if isinstance(value, CampaignConfigIn):
        return value.to_domain()
    if isinstance(value, dict):
        # Вложенная форма распознаётся по наличию доменных контейнеров.
        if "account" in value or "targeting" in value:
            return CampaignConfig.model_validate(value)
        return CampaignConfigIn.model_validate(value).to_domain()
    raise TypeError(f"config: ожидался dict/CampaignConfig, получено {type(value)!r}")


# ────────────────────────────── presets ──────────────────────────────


class PresetIn(BaseModel):
    """Тело создания/обновления пресета (стабильный конфиг залива)."""

    name: str = Field(min_length=1, max_length=255)
    act_id: str = Field(min_length=1, max_length=64)
    page_id: str = Field(min_length=1, max_length=64)
    pixel_id: str = Field(min_length=1, max_length=64)
    tz_offset: int = 0
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
    tz_offset: int
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
    total_bytes: int


# ────────────────────────────── validate ──────────────────────────────


class ValidateIn(BaseModel):
    """Запрос dry-run валидации конфига.

    `config` — каноническая ПЛОСКАЯ форма фронта (`CampaignConfigIn`) ИЛИ вложенный
    `CampaignConfig` (legacy). OpenAPI документирует обе (anyOf), фронтовые типы
    генерируются из плоской. `domain_config()`/`concept_counts_map()` нормализуют вход.
    """

    config: CampaignConfigIn | CampaignConfig
    concept_counts: dict[str, int] | None = None

    def domain_config(self) -> CampaignConfig:
        """Доменный CampaignConfig (плоский вход → to_domain, вложенный — как есть)."""
        return _coerce_to_domain(self.config)

    def concept_counts_map(self) -> dict[str, int] | None:
        """Число концептов на блок для build_campaign_spec.

        Приоритет: явный `concept_counts` из тела → из плоского входа (len concept_refs).
        Для вложенной формы None (раскладка по умолчанию 1 концепт/блок).
        """
        if self.concept_counts is not None:
            return self.concept_counts
        if isinstance(self.config, CampaignConfigIn):
            return self.config.concept_counts()
        return None


class AdsetPlanOut(BaseModel):
    """Сводка по одному adset в плане."""

    name: str
    status: str
    ad_count: int


class CampaignPlanOut(BaseModel):
    """Сводка по одной кампании в плане."""

    key: str
    name: str
    kind: str
    status: str
    adsets: list[AdsetPlanOut]


class ValidatePlanOut(BaseModel):
    """Результат validate: число объектов + нейминг без создания."""

    offer_code: str
    launch_state: str
    copies_per_concept: int
    campaign_count: int
    adset_count: int
    ad_count: int
    campaigns: list[CampaignPlanOut]


# ────────────────────────────── launch ──────────────────────────────


class LaunchIn(BaseModel):
    """Запрос запуска залива: конфиг + опц. ссылка на пресет/upload.

    `config` — каноническая ПЛОСКАЯ форма фронта (`CampaignConfigIn`) ИЛИ вложенный
    `CampaignConfig` (legacy). `domain_config()` нормализует в доменный CampaignConfig.
    `concept_counts_map()` даёт ту же раскладку K, что показал validate (симметрия превью↔залив).
    """

    config: CampaignConfigIn | CampaignConfig
    preset_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    concept_counts: dict[str, int] | None = None

    def domain_config(self) -> CampaignConfig:
        """Доменный CampaignConfig (единая точка нормализации входа)."""
        return _coerce_to_domain(self.config)

    def concept_counts_map(self) -> dict[str, int] | None:
        """Число концептов на блок для build_campaign_spec (как у ValidateIn).

        Приоритет: явный `concept_counts` из тела → из плоского входа (len concept_refs).
        Для вложенной формы None (раскладка по умолчанию 1 концепт/блок).
        """
        if self.concept_counts is not None:
            return self.concept_counts
        if isinstance(self.config, CampaignConfigIn):
            return self.config.concept_counts()
        return None


class LaunchOut(BaseModel):
    """Ответ запуска: id созданного run + id задачи."""

    run_id: str
    task_id: int | None
    status: str
    idempotency_key: str


# ────────────────────────────── runs ──────────────────────────────


class RunSummaryOut(BaseModel):
    """Краткая карточка запуска для списка."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    preset_id: str | None
    status: str
    offer_code: str | None
    idempotency_key: str | None
    error: str | None
    created_at: str
    updated_at: str


class RunDetailOut(BaseModel):
    """Детали запуска: конфиг-снимок + прогресс + Meta-ID + ошибка."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    preset_id: str | None
    status: str
    config: dict[str, Any]
    progress: dict[str, Any]
    created_meta_ids: dict[str, Any]
    error: str | None
    idempotency_key: str | None
    created_at: str
    updated_at: str


class CleanupOut(BaseModel):
    """Результат пометки run на снос Meta-объектов."""

    run_id: str
    meta_ids: dict[str, Any]  # созданные id для ручного/задачного сноса
    detail: str
