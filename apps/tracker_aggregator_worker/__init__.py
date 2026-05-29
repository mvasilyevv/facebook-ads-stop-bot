# -*- coding: utf-8 -*-
"""tracker_aggregator_worker — периодическая агрегация AdSet.pro postback'ов.

Раз в N минут пересчитывает tracker_aggregate per (ad_id, country, day) из
adsetpro_postback_events (см. core.adset_pro.aggregator). См. META_INTEGRATION_PLAN.md
§5 Волна 4 / Этап 6.
"""
