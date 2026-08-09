# -*- coding: utf-8 -*-
"""Каталог FB-иерархии: офферы, правила, кампании → adset'ы → объявления."""

from __future__ import annotations

from core.models.catalog.ad_account import AdAccount, OfferAdAccount
from core.models.catalog.fb_ad import FbAd
from core.models.catalog.fb_adset import FbAdset
from core.models.catalog.fb_campaign import FbCampaign
from core.models.catalog.offer import Offer
from core.models.catalog.offer_rule import OfferRule

__all__ = [
    "AdAccount",
    "FbAd",
    "FbAdset",
    "FbCampaign",
    "Offer",
    "OfferAdAccount",
    "OfferRule",
]
