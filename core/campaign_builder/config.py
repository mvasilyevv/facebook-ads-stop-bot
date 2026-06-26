# -*- coding: utf-8 -*-
"""Pydantic-конфиг создания FB-кампании (CampaignConfig).

Единый контракт API↔воркер↔движок. Извлечён из `scripts/fb_launch.py` без форка
логики: CLI импортирует эти модели. Дефолты — по SOP/памяти проекта
(`docs/playbooks/campaign-launch.md`):
- objective OUTCOME_SALES / optimization OFFSITE_CONVERSIONS / event PURCHASE;
- бюджет CBO, COST_CAP (требует bid_amount_cents), hard-cap валидация;
- таргет 21–65, advantage_audience, авто +AQ (Антарктида);
- атрибуция 1d click / 1d view;
- start_date = следующий день (today+1), дата в имени кампании = тот же день.

Money-инвариант (launch_state): по умолчанию `campaign_paused` — кампания PAUSED,
дети ACTIVE (модерация идёт, спенда нет; байер снимает паузу одним тумблером).
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import Enum

from pydantic import BaseModel, Field, model_validator

# Sane upper bounds — зеркало core/meta_api/mutations/set_adset_budget.py.
# Защита от лишнего нуля / hallucinated значения. Расширяется только в коде, не через payload.
MAX_DAILY_BUDGET_CENTS = 100_000_00  # $100 000 / день
MAX_LIFETIME_BUDGET_CENTS = 1_000_000_00  # $1 000 000 за весь период
MIN_DAILY_BUDGET_CENTS = 100  # $1.00 — ниже считаем опечаткой

# Стратегии ставок, которым обязателен bid_amount_cents.
_CAPPED_BID_STRATEGIES = frozenset({"COST_CAP", "LOWEST_COST_WITH_BID_CAP", "TARGET_COST"})


class LaunchState(str, Enum):
    """Money-инвариант запуска: что именно создаётся на паузе."""

    CAMPAIGN_PAUSED = "campaign_paused"  # кампания PAUSED, adset'ы+ads ACTIVE
    ALL_PAUSED = "all_paused"  # всё PAUSED


class Account(BaseModel):
    """Идентичность кабинета (preset)."""

    act_id: str  # с префиксом act_ или без — нормализуем
    page_id: str
    pixel_id: str
    tz_offset: str = "-07:00"  # TZ кабинета для start_time (America/Hermosillo = -07:00)

    @property
    def act(self) -> str:
        """act_id с гарантированным префиксом act_."""
        return self.act_id if self.act_id.startswith("act_") else f"act_{self.act_id}"

    @property
    def act_num(self) -> str:
        """Числовой act_id без префикса (для sub4 трекинга)."""
        return self.act_id.removeprefix("act_")


class Budget(BaseModel):
    """Бюджет и стратегия ставок с hard-cap валидацией (money-safe)."""

    level: str = "campaign"  # campaign (CBO) | adset (ABO)
    daily_cents: int = 300
    lifetime_cents: int | None = None
    bid_strategy: str = "COST_CAP"  # SOP: реальные кампании кабинета всегда COST_CAP
    bid_amount_cents: int | None = None  # для COST_CAP / BID_CAP / TARGET_COST (обязателен)

    @model_validator(mode="after")
    def _check(self) -> Budget:
        if self.level not in ("campaign", "adset"):
            raise ValueError("budget.level: campaign | adset")
        if self.bid_strategy in _CAPPED_BID_STRATEGIES and not self.bid_amount_cents:
            raise ValueError(f"bid_strategy={self.bid_strategy} требует bid_amount_cents")
        if self.daily_cents < MIN_DAILY_BUDGET_CENTS:
            raise ValueError(
                f"daily_cents < {MIN_DAILY_BUDGET_CENTS} (${MIN_DAILY_BUDGET_CENTS / 100:.2f}) — "
                "проверь бюджет"
            )
        if self.daily_cents > MAX_DAILY_BUDGET_CENTS:
            raise ValueError(
                f"daily_cents > hard-cap {MAX_DAILY_BUDGET_CENTS} "
                f"(${MAX_DAILY_BUDGET_CENTS / 100:,.0f}/день) — отклонено"
            )
        if self.lifetime_cents is not None and self.lifetime_cents > MAX_LIFETIME_BUDGET_CENTS:
            raise ValueError(
                f"lifetime_cents > hard-cap {MAX_LIFETIME_BUDGET_CENTS} "
                f"(${MAX_LIFETIME_BUDGET_CENTS / 100:,.0f}) — отклонено"
            )
        return self


class Targeting(BaseModel):
    """Таргет с авто-добавлением Антарктиды (+AQ) по SOP."""

    countries: list[str]
    add_antarctica: bool = True  # SOP: AQ всегда
    age_min: int = 21  # SOP: дефолт 21 (реальные кампании кабинета)
    age_max: int = 65
    location_types: list[str] = Field(default_factory=lambda: ["home", "recent"])
    advantage_audience: bool = True

    def geo_countries(self) -> list[str]:
        """Список стран с авто-AQ (без дубля)."""
        countries = list(self.countries)
        if self.add_antarctica and "AQ" not in countries:
            countries.append("AQ")
        return countries


class Attribution(BaseModel):
    """Окно атрибуции конверсий (preset)."""

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


def _default_start_date() -> str:
    """Дефолт start_date = today+1 (UTC), по SOP."""
    return (date.today() + timedelta(days=1)).isoformat()


class CampaignConfig(BaseModel):
    """Полный конфиг создания кампании (preset + run в одной модели)."""

    account: Account
    offer_code: str
    byer_tag: str = "MV"
    objective: str = "OUTCOME_SALES"
    optimization_goal: str = "OFFSITE_CONVERSIONS"
    custom_event_type: str = "PURCHASE"
    special_ad_categories: list[str] = Field(default_factory=lambda: ["NONE"])
    destination_link: str
    cta: str = "PLAY_GAME"
    text_optimizations: str = "OPT_OUT"
    start_date: str | None = Field(default_factory=_default_start_date)  # YYYY-MM-DD = today+1
    creo_root: str = ""
    # budget обязателен: дефолт-стратегия COST_CAP требует bid_amount_cents, поэтому
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
    launch_state: LaunchState = LaunchState.CAMPAIGN_PAUSED

    @model_validator(mode="after")
    def _check(self) -> CampaignConfig:
        # start_date может прийти None из API (опциональное поле) — подставим дефолт.
        if not self.start_date:
            self.start_date = _default_start_date()
        # Грубая проверка формата YYYY-MM-DD.
        parts = self.start_date.split("-")
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"start_date должен быть YYYY-MM-DD, получено {self.start_date!r}")
        if self.copies_per_concept is not None and self.copies_per_concept < 1:
            raise ValueError("copies_per_concept должен быть >= 1")
        return self

    @property
    def start_time(self) -> str:
        """ISO8601 start_time с tz-offset кабинета."""
        return f"{self.start_date}T00:00:00{self.account.tz_offset}"

    @property
    def date_label(self) -> str:
        """YYYY-MM-DD → DD.MM для имени кампании."""
        y, m, d = self.start_date.split("-")
        return f"{d}.{m}"
