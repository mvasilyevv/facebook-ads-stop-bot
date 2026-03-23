from __future__ import annotations

from decimal import Decimal

from apps.browser_host.facebook_payload_collector import (
    has_unresolved_scope_rows,
    parse_ads_manager_payloads,
)


# Проверяет, что payload collector объединяет tabular-метрики и GraphQL-связи в одну полную строку объявления.
def test_parse_ads_manager_payloads_merges_tabular_metrics_with_graphql_scope() -> None:
    rows = parse_ads_manager_payloads(
        relevant_payloads=[
            {
                "data": [
                    {
                        "rows": [
                            {
                                "dimension_values": {
                                    "ad_id": "120241420867550176",
                                    "ad_name": "DRC_CR2_CR015",
                                    "delivery_status": "Выключено",
                                },
                                "atomic_values": {
                                    "spend": "0.16 $",
                                    "clicks": "1",
                                    "cpc": "0.16 $",
                                },
                            }
                        ]
                    }
                ]
            }
        ],
        all_payloads=[
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "AdCampaignGroup",
                            "id": "120241420128910176",
                            "name": "CR2 | DRC | MV | NEW | pwa.partners | 15.03",
                        },
                        {
                            "__typename": "AdCampaign",
                            "id": "120241420867510000",
                            "name": "3",
                            "ad_campaign_group_id": "120241420128910176",
                        },
                        {
                            "__typename": "Adgroup",
                            "id": "120241420867550176",
                            "name": "DRC_CR2_CR015",
                            "ad_campaign_name": "3",
                            "ad_campaign_id": "120241420867510000",
                            "delivery_status": {"status": "PAUSED"},
                        },
                    ]
                }
            }
        ],
        page_url="https://adsmanager.facebook.com/adsmanager/manage/ads",
    )

    assert len(rows) == 1
    assert rows[0].fb_ad_id == "120241420867550176"
    assert rows[0].campaign_name == "CR2 | DRC | MV | NEW | pwa.partners | 15.03"
    assert rows[0].adset_name == "3"
    assert rows[0].ad_name == "DRC_CR2_CR015"
    assert rows[0].spend == Decimal("0.16")
    assert rows[0].clicks == 1
    assert rows[0].cpc == Decimal("0.16")


# Проверяет, что payload collector умеет помечать строки с placeholder-именами как неразрешенный scope.
def test_has_unresolved_scope_rows_detects_placeholder_names() -> None:
    rows = parse_ads_manager_payloads(
        relevant_payloads=[
            {
                "data": [
                    {
                        "rows": [
                            {
                                "dimension_values": {
                                    "ad_id": "120241420867550176",
                                    "ad_name": "DRC_CR2_CR015",
                                    "delivery_status": "Выключено",
                                },
                                "atomic_values": {
                                    "spend": "0.00 $",
                                    "clicks": "0",
                                    "cpc": "—",
                                },
                            }
                        ]
                    }
                ]
            }
        ],
        all_payloads=[],
        page_url=(
            "https://adsmanager.facebook.com/adsmanager/manage/ads"
            "?selected_campaign_ids=120241420128910176"
        ),
    )

    assert has_unresolved_scope_rows(rows) is True
