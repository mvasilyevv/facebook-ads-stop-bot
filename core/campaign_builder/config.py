# -*- coding: utf-8 -*-
"""Pydantic-конфиг создания FB-кампании (CampaignConfig).

Единый контракт API↔воркер↔движок. Дефолты — по SOP проекта
(`docs/playbooks/campaign-launch.md`):
- objective OUTCOME_SALES / optimization OFFSITE_CONVERSIONS / event PURCHASE;
- бюджет CBO, COST_CAP (требует bid_amount), hard-cap в major units;
- таргет 21–65, advantage_audience, авто +AQ (Антарктида);
- атрибуция 1d click / 1d view;
- start_date = следующий день (today+1), дата в имени кампании = тот же день.

Money-инвариант: campaign, ad set и ad всегда создаются PAUSED. Активация
разрешена только отдельным ручным действием после review.
"""

from __future__ import annotations

import re
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.campaign_builder.money import (
    campaign_currency_exponent,
    major_amount_to_minor_units,
    normalize_major_amount,
)
from core.meta_api.account_tz import canonical_account_id, validated_timezone_name
from core.money import validated_currency_code

# Currency-neutral major-unit caps.  Conversion to Meta minor units happens
# only after the cabinet currency and its explicit exponent are confirmed.
MAX_DAILY_BUDGET = Decimal("100000")
MAX_LIFETIME_BUDGET = Decimal("1000000")

# Стратегии ставок, которым обязателен bid_amount.
_CAPPED_BID_STRATEGIES = frozenset({"COST_CAP", "LOWEST_COST_WITH_BID_CAP", "TARGET_COST"})
CAMPAIGN_GENDERS = frozenset({"male", "female"})
CAMPAIGN_PLACEMENTS = frozenset({"facebook", "instagram", "messenger", "audience_network"})
_META_GENDER_IDS = {"male": 1, "female": 2}


class Account(BaseModel):
    """Immutable cabinet identity and server-confirmed account evidence."""

    model_config = ConfigDict(extra="forbid")

    act_id: str  # с префиксом act_ или без — нормализуем
    page_id: str
    pixel_id: str
    timezone_name: str
    currency: str
    currency_exponent: int | None = None
    account_context_observed_at: datetime

    @model_validator(mode="after")
    def _validate_context(self) -> Account:
        account_id = canonical_account_id(self.act_id)
        if re.fullmatch(r"[0-9]{1,32}", account_id) is None:
            raise ValueError("account.act_id must contain 1..32 digits")
        timezone_name = validated_timezone_name(self.timezone_name)
        if timezone_name is None:
            raise ValueError("account.timezone_name must be a validated IANA timezone")
        currency = validated_currency_code(self.currency)
        if currency is None:
            raise ValueError("account.currency must be a validated ISO 4217 code")
        exponent = campaign_currency_exponent(currency)
        if self.currency_exponent is not None and self.currency_exponent != exponent:
            raise ValueError("account.currency_exponent does not match account.currency")
        if self.account_context_observed_at.tzinfo is None:
            raise ValueError("account_context_observed_at must be timezone-aware")
        self.act_id = account_id
        self.timezone_name = timezone_name
        self.currency = currency
        self.currency_exponent = exponent
        return self

    @property
    def act(self) -> str:
        """act_id с гарантированным префиксом act_."""
        return self.act_id if self.act_id.startswith("act_") else f"act_{self.act_id}"

    @property
    def act_num(self) -> str:
        """Числовой act_id без префикса (для sub4 трекинга)."""
        return self.act_id.removeprefix("act_")


class Budget(BaseModel):
    """Currency-bound major-unit amounts with exact Meta conversion."""

    model_config = ConfigDict(extra="forbid")

    level: str = "campaign"  # campaign (CBO) | adset (ABO)
    currency: str
    daily_amount: str
    lifetime_amount: str | None = None
    bid_strategy: str = "COST_CAP"  # SOP: реальные кампании кабинета всегда COST_CAP
    bid_amount: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Budget:
        if self.level not in ("campaign", "adset"):
            raise ValueError("budget.level: campaign | adset")
        currency = validated_currency_code(self.currency)
        if currency is None:
            raise ValueError("budget.currency must be a validated ISO 4217 code")
        campaign_currency_exponent(currency)
        self.currency = currency
        self.daily_amount = normalize_major_amount(self.daily_amount, currency=currency)
        daily = Decimal(self.daily_amount)
        if daily > MAX_DAILY_BUDGET:
            raise ValueError(f"daily_amount exceeds the {MAX_DAILY_BUDGET} {currency} hard cap")
        if self.lifetime_amount is not None:
            self.lifetime_amount = normalize_major_amount(
                self.lifetime_amount,
                currency=currency,
            )
            if Decimal(self.lifetime_amount) > MAX_LIFETIME_BUDGET:
                raise ValueError(
                    f"lifetime_amount exceeds the {MAX_LIFETIME_BUDGET} {currency} hard cap"
                )
        if self.bid_strategy in _CAPPED_BID_STRATEGIES and not self.bid_amount:
            raise ValueError(f"bid_strategy={self.bid_strategy} requires bid_amount")
        if self.bid_amount is not None:
            self.bid_amount = normalize_major_amount(self.bid_amount, currency=currency)
        return self

    @property
    def daily_minor_units(self) -> int:
        return major_amount_to_minor_units(self.daily_amount, currency=self.currency)

    @property
    def lifetime_minor_units(self) -> int | None:
        if self.lifetime_amount is None:
            return None
        return major_amount_to_minor_units(self.lifetime_amount, currency=self.currency)

    @property
    def bid_minor_units(self) -> int | None:
        if self.bid_amount is None:
            return None
        return major_amount_to_minor_units(self.bid_amount, currency=self.currency)


class Targeting(BaseModel):
    """Таргет с авто-добавлением Антарктиды (+AQ) по SOP."""

    model_config = ConfigDict(extra="forbid")

    countries: list[str]
    add_antarctica: bool = True  # SOP: AQ всегда
    age_min: int = 21  # SOP: дефолт 21 (реальные кампании кабинета)
    age_max: int = 65
    location_types: list[str] = Field(default_factory=lambda: ["home", "recent"])
    advantage_audience: bool = True
    # Значения сняты с 360 живых групп кабинетов (замер 17.08): без них наша
    # группа уходит в Meta не такой, как те, что реально откручиваются.
    # expansion_all — расширение аудитории за пределы заданной, 360 из 360.
    targeting_optimization: str = "expansion_all"
    # FACEBOOK_RELAXED/AN_RELAXED — минимальная фильтрация контента, 345 из 360.
    brand_safety_relaxed: bool = True
    # Пустой список сохраняет Meta automatic placements / all genders.
    # publisher_platforms не встречается ни в одной из 360 живых групп:
    # площадки там отдаются автоматике, а не перечисляются руками.
    genders: list[str] = Field(default_factory=list)
    placements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_repeated_targeting(self) -> Targeting:
        if self.age_min > self.age_max:
            raise ValueError("targeting.age_min must not exceed targeting.age_max")
        if len(self.genders) != len(set(self.genders)) or not set(self.genders) <= CAMPAIGN_GENDERS:
            raise ValueError("targeting.genders contains an unsupported or duplicate value")
        if (
            len(self.placements) != len(set(self.placements))
            or not set(self.placements) <= CAMPAIGN_PLACEMENTS
        ):
            raise ValueError("targeting.placements contains an unsupported or duplicate value")
        return self

    def geo_countries(self) -> list[str]:
        """Список стран с авто-AQ (без дубля)."""
        countries = list(self.countries)
        if self.add_antarctica and "AQ" not in countries:
            countries.append("AQ")
        return countries

    def gender_ids(self) -> list[int]:
        """Meta targeting gender ids; empty means all genders."""
        return [_META_GENDER_IDS[value] for value in self.genders]


class Attribution(BaseModel):
    """Окно атрибуции конверсий (preset)."""

    model_config = ConfigDict(extra="forbid")

    click_through_days: int = 1
    view_through_days: int = 1

    def spec(self) -> list[dict]:
        """attribution_spec для тела adset."""
        out: list[dict] = []
        if self.click_through_days:
            out.append({"event_type": "CLICK_THROUGH", "window_days": self.click_through_days})
        if self.view_through_days:
            out.append({"event_type": "VIEW_THROUGH", "window_days": self.view_through_days})
        return out


class AdText(BaseModel):
    """Текст объявления (run). mode=none — объявления без текста."""

    model_config = ConfigDict(extra="forbid")

    mode: str = "none"  # none | full
    message: str = ""
    headline: str = ""
    description: str = ""

    @model_validator(mode="after")
    def _check(self) -> AdText:
        if self.mode not in ("none", "full"):
            raise ValueError("ad_text.mode: none | full")
        return self


class AdsetConfig(BaseModel):
    """Один adset в структуре кампании."""

    model_config = ConfigDict(extra="forbid")

    name: str  # шаблон имени с плейсхолдерами
    dir: str  # подпапка концептов относительно creo_root
    glob: str  # маска файлов


# Расширения медиа для определения типа концепта по имени файла (единый источник:
# воркер и валидатор импортят отсюда). Тип ad'а выбирается per-concept по расширению.
# Файл с неизвестным расширением reject'ится валидатором CampaignBlock ДО любого POST
# (защита уникализатора PIL/ffmpeg от орфанов в Meta).
VIDEO_EXTS = frozenset({".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"})
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})


def ref_media_kind(ref: str) -> str | None:
    """Тип медиа по расширению имени файла: 'video' | 'image' | None (неизвестно)."""
    suffix = ("." + ref.rsplit(".", 1)[-1].lower()) if "." in ref else ""
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in IMAGE_EXTS:
        return "image"
    return None


class CampaignBlock(BaseModel):
    """Одна кампания: список adset'ов + смешанный набор концептов (фото/видео).

    Тип каждого ad определяется по расширению файла концепта (ref_media_kind),
    не по кампании. concept_refs — единый источник концептов блока.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    name: str  # шаблон имени с плейсхолдерами
    adsets: list[AdsetConfig]
    concept_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> CampaignBlock:
        # Money-safety: уникализатор умеет только image (PIL) и video (ffmpeg).
        # Файл с неизвестным расширением уронил бы материализацию уже ПОСЛЕ
        # создания объектов в Meta → орфаны. Отклоняем ДО любого POST.
        for ref in self.concept_refs:
            if ref_media_kind(ref) is None:
                raise ValueError(
                    f"кампания {self.key!r}: концепт {ref!r} имеет неизвестное "
                    f"расширение — поддерживаются только фото и видео"
                )
        return self


class CampaignConfig(BaseModel):
    """Полный конфиг создания кампании (preset + run в одной модели)."""

    model_config = ConfigDict(extra="forbid")

    account: Account
    offer_code: str
    byer_tag: str = "MV"
    objective: str = "OUTCOME_SALES"
    optimization_goal: str = "OFFSITE_CONVERSIONS"
    custom_event_type: str = "PURCHASE"
    special_ad_categories: list[str] = Field(default_factory=lambda: ["NONE"])
    destination_link: str
    # Опциональный готовый query-template для url_tags. Когда не задан, builder
    # формирует SOP-набор sub2..sub8. Кастомный шаблон остаётся opaque-строкой:
    # builder лишь гарантирует наличие стабильного Meta ad id в sub8.
    url_tags_template: str | None = Field(default=None, max_length=1024)
    cta: str = "PLAY_GAME"
    text_optimizations: str = "OPT_OUT"
    start_date: str
    creo_root: str = ""
    # budget обязателен: дефолт-стратегия COST_CAP требует bid_amount, поэтому
    # «пустого» дефолтного бюджета у money-конфига быть не может — задаётся явно.
    budget: Budget
    targeting: Targeting
    attribution: Attribution = Field(default_factory=Attribution)
    ad_text: AdText = Field(default_factory=AdText)
    campaigns: list[CampaignBlock]
    copies_per_concept: int | None = None  # default = число adset'ов кампании
    creative_prefix: str = ""  # префикс кодов креативов (default = {offer}_CR)
    # База сквозной нумерации кодов креативов. =1 по умолчанию; на launch
    # аллокатор per-offer проставляет реальное смещение (см. campaigns_create.launch).
    code_start: int = 1

    @model_validator(mode="after")
    def _check(self) -> CampaignConfig:
        try:
            datetime.strptime(self.start_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("start_date must be a real YYYY-MM-DD date") from exc
        if self.account.currency != self.budget.currency:
            raise ValueError("budget currency does not match confirmed cabinet currency")
        if self.copies_per_concept is not None and self.copies_per_concept < 1:
            raise ValueError("copies_per_concept должен быть >= 1")
        return self

    @property
    def start_time(self) -> str:
        """Cabinet-local midnight using IANA rules for the selected date."""

        local_date = datetime.strptime(self.start_date, "%Y-%m-%d").date()
        local_midnight = datetime.combine(
            local_date,
            time.min,
            tzinfo=ZoneInfo(self.account.timezone_name),
        )
        return local_midnight.isoformat()

    @property
    def date_label(self) -> str:
        """YYYY-MM-DD → DD.MM для имени кампании."""
        y, m, d = self.start_date.split("-")
        return f"{d}.{m}"
