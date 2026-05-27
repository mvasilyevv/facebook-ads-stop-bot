# -*- coding: utf-8 -*-
"""End-to-end Ad Library pipeline через fake gRPC + real Postgres.

Поднимает реальную БД из docker-compose:5433, мокает только AdLibraryClient (gRPC).
Проверяет что: scan создаётся, snapshot/ad/tier/report пишутся, классификация работает.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text


# Сценарий: пустой pool — pipeline должен корректно завершиться без падений,
# scan статус 'done', ad_count=0, tier таблица пустая, report сгенерирован
@pytest.mark.asyncio
async def test_pipeline_empty_pool(
    pg_engine, fake_ad_lib_client, fake_ad_lib_scenario, clean_ad_library_tables
) -> None:
    from core.ad_library.pipeline import run_pipeline

    fake_ad_lib_scenario.ad_count = 0
    fake_ad_lib_scenario.ads = []

    result = await run_pipeline(
        pg_engine,
        slot="nonexistent slot",
        country="ZZ",
        triggered_by="test",
        skip_media=True,
    )

    assert result.scan.status == "done"
    assert result.scan.ads_count == 0
    assert result.scan.slot == "nonexistent slot"
    assert result.scan.country == "ZZ"

    async with pg_engine.connect() as conn:
        n_ads = (await conn.execute(text("SELECT COUNT(*) FROM ad_library_ad"))).scalar()
        n_snapshots = (
            await conn.execute(text("SELECT COUNT(*) FROM ad_library_snapshot"))
        ).scalar()
        n_tier = (await conn.execute(text("SELECT COUNT(*) FROM ad_library_tier"))).scalar()
        n_reports = (await conn.execute(text("SELECT COUNT(*) FROM ad_library_report"))).scalar()

    assert n_ads == 0
    assert n_snapshots == 0
    assert n_tier == 0
    # Контракт pipeline.py: для empty pool возвращается ранний exit без report —
    # «честный empty result, никакого fallback на расширение запроса».
    assert n_reports == 0


# Сценарий: 3 ads с разными характеристиками → snapshots/ads/tiers создались,
# tier-ranker отработал, report содержит markdown
@pytest.mark.asyncio
async def test_pipeline_with_ads(
    pg_engine, fake_ad_lib_client, fake_ad_lib_scenario, clean_ad_library_tables
) -> None:
    from core.ad_library.pipeline import run_pipeline

    # Эмулируем GraphQL-ответ Meta Ad Library.
    # Контракт tier_ranker: S требует days_running >= 30 AND (page_history >= 2 OR cluster >= 2).
    # Два ad'а с одной page_id (777) → cluster_size=2 → второй критерий S выполнен для них.
    fake_ads = [
        {
            "ad_archive_id": "111111111",
            "page_id": "777",
            "page_name": "Casino Winners KE",
            "snapshot": {
                "page_name": "Casino Winners KE",
                "body": {"text": "Chicken Road 2 — winner!"},
                "title": "Chicken Road 2 bonus",
                "videos": [{"video_hd_url": "https://example.com/v.mp4"}],
            },
            "start_date": "2026-04-01",  # 55 дней + cluster=2 → S-tier
        },
        {
            "ad_archive_id": "111111112",
            "page_id": "777",  # та же страница — формирует cluster_size=2 для S
            "page_name": "Casino Winners KE",
            "snapshot": {
                "page_name": "Casino Winners KE",
                "body": {"text": "Chicken Road 2 variation"},
                "videos": [{"video_hd_url": "https://example.com/v2.mp4"}],
            },
            "start_date": "2026-04-05",
        },
        {
            "ad_archive_id": "222222222",
            "page_id": "888",
            "page_name": "Bet Kenya",
            "snapshot": {
                "page_name": "Bet Kenya",
                "body": {"text": "Chicken Road 2 — играй сейчас"},
                "images": [{"original_image_url": "https://example.com/i.jpg"}],
            },
            "start_date": "2026-05-20",  # ~7 дней, без cluster → B-tier
        },
        {
            "ad_archive_id": "333333333",
            "page_id": "999",
            "page_name": "Random Page Without Match",
            "snapshot": {
                "page_name": "Random Page",
                "body": {"text": "что-то совсем не про слоты"},
            },
            # без start_date → C-tier
        },
    ]
    fake_ad_lib_scenario.ad_count = len(fake_ads)
    fake_ad_lib_scenario.ads = fake_ads

    result = await run_pipeline(
        pg_engine,
        slot="chicken road 2",
        country="KE",
        triggered_by="test",
        skip_media=True,
    )

    assert result.scan.status == "done"
    assert result.scan.ads_count == 4

    async with pg_engine.connect() as conn:
        ad_rows = (
            await conn.execute(
                text(
                    "SELECT ad_archive_id, slot, country, page_name "
                    "FROM ad_library_ad ORDER BY ad_archive_id"
                )
            )
        ).all()
        snap_rows = (
            await conn.execute(
                text("SELECT scan_id, ad_archive_id, is_active FROM ad_library_snapshot")
            )
        ).all()
        tier_rows = (
            await conn.execute(text("SELECT tier, ad_archive_id, score FROM ad_library_tier"))
        ).all()
        report_row = (
            await conn.execute(
                text(
                    "SELECT markdown_report, jsonb_array_length(top_winners_json) "
                    "FROM ad_library_report"
                )
            )
        ).first()

    # Контракт: каждый ad из GraphQL → строка в ad_library_ad
    assert len(ad_rows) == 4
    assert {r[0] for r in ad_rows} == {111111111, 111111112, 222222222, 333333333}

    # Контракт: slot/country записаны ДОСЛОВНО как в запросе пользователя
    for r in ad_rows:
        assert r[1] == "chicken road 2", f"slot искажён: {r[1]!r}"
        assert r[2] == "KE"

    # Контракт: на scan приходится N snapshot'ов = N ads
    assert len(snap_rows) == 4
    assert all(r[2] is True for r in snap_rows)

    # Контракт: tier для каждого ad'а посчитан
    assert len(tier_rows) == 4

    # Контракт: 2 ad'а с одной page_id + 55 дней running → оба попадают в S-tier
    s_tier_ads = {r[1] for r in tier_rows if r[0] == "S"}
    assert 111111111 in s_tier_ads and 111111112 in s_tier_ads, (
        f"cluster size 2 + 55 days должно дать S, получили tiers={tier_rows}"
    )

    # Контракт: report сгенерирован с непустым markdown'ом
    assert report_row is not None
    md, n_winners = report_row
    assert "chicken road 2" in md.lower()
    assert "KE" in md
    assert n_winners >= 1


# Сценарий: gRPC падает с network error — pipeline помечает scan 'failed',
# но не крашится наружу
@pytest.mark.asyncio
async def test_pipeline_handles_grpc_error(
    pg_engine, fake_ad_lib_client, fake_ad_lib_scenario, clean_ad_library_tables
) -> None:
    from core.ad_library.pipeline import run_pipeline

    fake_ad_lib_scenario.raise_error = RuntimeError("simulated gRPC timeout")

    result = await run_pipeline(
        pg_engine,
        slot="failure case",
        country="KE",
        triggered_by="test",
        skip_media=True,
    )

    assert result.scan.status == "failed"
    assert result.scan.error is not None
    assert "simulated gRPC timeout" in result.scan.error

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, error_message FROM ad_library_scan WHERE id = :sid"),
                {"sid": result.scan.scan_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "failed"
    assert "simulated gRPC timeout" in (row[1] or "")


# Сценарий: правило «slot+country дословно» — даже если у пользователя
# `Chicken Road 2` с разным регистром, в БД записываем как пришло (нижний регистр после .strip())
@pytest.mark.asyncio
async def test_pipeline_preserves_user_input_verbatim(
    pg_engine, fake_ad_lib_client, fake_ad_lib_scenario, clean_ad_library_tables
) -> None:
    from core.ad_library.pipeline import run_pipeline

    fake_ad_lib_scenario.ad_count = 1
    fake_ad_lib_scenario.ads = [
        {
            "ad_archive_id": "555",
            "page_id": "1",
            "page_name": "Page",
            "snapshot": {"page_name": "Page", "body": {"text": "hi"}},
        }
    ]

    # Slot со смешанным регистром + пробелы — scanner не должен «угадывать» что-то другое
    result = await run_pipeline(
        pg_engine,
        slot="Chicken Road 2",
        country="ke",  # нижний регистр — нормализуется в scanner.py до "KE"
        triggered_by="test",
        skip_media=True,
    )

    assert result.scan.slot == "Chicken Road 2"
    assert result.scan.country == "KE"
