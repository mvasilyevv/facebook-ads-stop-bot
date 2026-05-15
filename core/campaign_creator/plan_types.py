# -*- coding: utf-8 -*-
"""Типы данных декларативного плана creator'а."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class PlanAction:
    step: str
    params: dict[str, Any] = field(default_factory=dict)
    idempotent: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PlanAction":
        return cls(
            step=d["step"],
            params=dict(d.get("params") or {}),
            idempotent=bool(d.get("idempotent", True)),
        )


@dataclass
class AdsetSpec:
    name_suffix: str
    creo_subfolder: str
    headline: str
    primary_text: str
    creatives: list[str]


@dataclass
class CampaignSpec:
    offer_code: str
    cabinet_id: str
    pixel_id: str
    landing_url: str
    countries: list[str]
    daily_budget: float
    attribution_days: Literal[1, 7]
    budget_level: Literal["CBO", "ABO"]
    adsets: list[AdsetSpec]
    campaign_name: str | None = None
    iter_num: int = 1


@dataclass
class FBState:
    done_indices: set[int] = field(default_factory=set)
    current_adset_idx: int | None = None
    current_ad_idx: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def mark_done(self, idx: int) -> None:
        self.done_indices.add(idx)

    def is_done(self, idx: int) -> bool:
        return idx in self.done_indices
