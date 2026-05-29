# -*- coding: utf-8 -*-
"""Unit-тесты owner-scoping: campaign_matches_owner (отсев чужих кампаний в общем кабинете).

Контекст: рекламный кабинет общий, в нём кампании разных команд. Бот должен работать
ТОЛЬКО со своими (тег владельца в названии, например 'MV'). Чужие — полный игнор.
"""

from __future__ import annotations

from core.observer.queries import campaign_matches_owner, parse_owner_tags


# Моя кампания с тегом MV первым сегментом — распознаётся как своя
def test_owner_tag_matches_my_campaign() -> None:
    assert campaign_matches_owner(
        campaign_name="MV | GH | CR2 | adset.pro | 22.05 | 1",
        ad_name="GH_CR2_CR005",
        owner_tag="MV",
    )


# Чужая кампания MZ Artemteam НЕ должна матчиться тегом MV (word-boundary: MZ != MV)
def test_owner_tag_rejects_foreign_similar_tag() -> None:
    assert not campaign_matches_owner(
        campaign_name="14.05 MZ Artemteam CBO 1-3-1", ad_name="some_ad", owner_tag="MV"
    )


# Совсем чужая кампания без тега — не моя
def test_owner_tag_rejects_unrelated_campaign() -> None:
    assert not campaign_matches_owner(
        campaign_name="ls_aviator_ivan_team_05_28(4)", ad_name="aviator_1", owner_tag="MV"
    )


# Тег внутри слова (MVP) не считается совпадением — только word-boundary
def test_owner_tag_not_substring_inside_word() -> None:
    assert not campaign_matches_owner(
        campaign_name="MVP launch campaign", ad_name="mvp_ad", owner_tag="MV"
    )


# Тег может быть в ad_name (если нейминг кампании без тега, но ad содержит)
def test_owner_tag_matches_in_ad_name() -> None:
    assert campaign_matches_owner(
        campaign_name="generic campaign", ad_name="MV_GH_CR2_001", owner_tag="MV"
    )


# Матчинг регистронезависимый
def test_owner_tag_case_insensitive() -> None:
    assert campaign_matches_owner(campaign_name="mv | gh | cr2", ad_name="x", owner_tag="MV")
    assert campaign_matches_owner(campaign_name="MV | GH | CR2", ad_name="x", owner_tag="mv")


# Пустой/None owner_tag → фильтр выключен, любая кампания проходит (обратная совместимость)
def test_owner_tag_disabled_when_empty() -> None:
    assert campaign_matches_owner(campaign_name="MZ Artemteam", ad_name="x", owner_tag=None)
    assert campaign_matches_owner(campaign_name="MZ Artemteam", ad_name="x", owner_tag="")
    assert campaign_matches_owner(campaign_name="MZ Artemteam", ad_name="x", owner_tag="   ")


# parse_owner_tags: CSV (запятая/точка-с-запятой) → список непустых тегов
def test_parse_owner_tags() -> None:
    assert parse_owner_tags("MV") == ["MV"]
    assert parse_owner_tags("MV,ABC") == ["MV", "ABC"]
    assert parse_owner_tags("MV, ABC ; XYZ") == ["MV", "ABC", "XYZ"]
    assert parse_owner_tags("") == []
    assert parse_owner_tags(None) == []


# Мультитег: кампания отслеживается при совпадении с ЛЮБЫМ тегом из списка
def test_owner_multitag_matches_any() -> None:
    tags = "MV,ABC"
    # MV-кампания — своя
    assert campaign_matches_owner(campaign_name="MV | GH | CR2", ad_name="x", owner_tag=tags)
    # ABC-кампания — тоже своя (второй тег)
    assert campaign_matches_owner(campaign_name="ABC | KE | XX", ad_name="y", owner_tag=tags)
    # тег может быть в ad_name
    assert campaign_matches_owner(campaign_name="generic", ad_name="ABC_KE_001", owner_tag=tags)
    # ни MV ни ABC → чужая
    assert not campaign_matches_owner(campaign_name="MZ Artemteam", ad_name="z", owner_tag=tags)
    # тег не из списка (XYZ) → не матчит
    assert not campaign_matches_owner(campaign_name="XYZ | foo", ad_name="z", owner_tag=tags)
