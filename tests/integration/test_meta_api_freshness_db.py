"""PostgreSQL proof for the final automatic-pause snapshot gate."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from core.meta_api.freshness import load_meta_snapshot_freshness


@pytest.mark.asyncio
async def test_auto_pause_requires_current_complete_scan_and_rule_generation(pg_engine) -> None:
    checked_at = datetime.now(UTC)
    cycle_at = checked_at - timedelta(seconds=30)
    account_id = str(10**17 + uuid.uuid4().int % (9 * 10**17))
    fb_ad_id = str(10**17 + uuid.uuid4().int % (9 * 10**17))
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    async with pg_engine.connect() as conn:
        previous_scanning_enabled = await conn.scalar(
            text("SELECT is_scanning_enabled FROM observer_config WHERE singleton_key = 'default'")
        )
    config_existed = previous_scanning_enabled is not None

    scan_id: int | None = None
    try:
        async with pg_engine.begin() as conn:
            scan_id = int(await conn.scalar(text("SELECT nextval('scan_runs_id_seq')")))
            await conn.execute(
                text(
                    "INSERT INTO offers (id, code, name, is_active) "
                    "VALUES (:id, :code, 'Freshness proof', TRUE)"
                ),
                {"id": offer_id, "code": f"FRESH_{offer_id.hex[:10]}"},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO offer_rules
                        (offer_id, cpa_threshold, currency, updated_at)
                    VALUES (:offer_id, 10, 'USD', :updated_at)
                    """
                ),
                {"offer_id": offer_id, "updated_at": cycle_at - timedelta(minutes=1)},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_campaigns
                        (id, campaign_name, offer_id, ad_account_id)
                    VALUES (:id, 'Freshness campaign', :offer_id, :account_id)
                    """
                ),
                {"id": campaign_id, "offer_id": offer_id, "account_id": account_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO fb_adsets (id, campaign_id, adset_name) "
                    "VALUES (:id, :campaign_id, 'Freshness adset')"
                ),
                {"id": adset_id, "campaign_id": campaign_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name)
                    VALUES (:id, :adset_id, :fb_ad_id, 'Freshness ad')
                    """
                ),
                {"id": ad_id, "adset_id": adset_id, "fb_ad_id": fb_ad_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO scan_runs
                        (id, scan_id, started_at, finished_at, outcome,
                         rows_total, ad_account_id)
                    VALUES
                        (:scan_id, :scan_id, :started_at, :finished_at, 'success',
                         1, :account_id)
                    """
                ),
                {
                    "scan_id": scan_id,
                    "started_at": cycle_at - timedelta(seconds=1),
                    "finished_at": cycle_at + timedelta(seconds=1),
                    "account_id": account_id,
                },
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics
                        (ad_id, cycle_ts, scan_id, currency, spend, clicks)
                    VALUES (:ad_id, :cycle_ts, :scan_id, 'USD', 15, 10)
                    """
                ),
                {"ad_id": ad_id, "cycle_ts": cycle_at, "scan_id": scan_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_alert_state
                        (ad_id, alert_state, current_stage, stop_rule_codes,
                         last_scan_id, last_transition_at)
                    VALUES
                        (:ad_id, 'stop_sent', 'stop', '["cpa_stop"]'::jsonb,
                         :scan_id, :cycle_ts)
                    """
                ),
                {"ad_id": ad_id, "scan_id": scan_id, "cycle_ts": cycle_at},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO observer_config
                        (id, singleton_key, is_scanning_enabled)
                    VALUES (gen_random_uuid(), 'default', TRUE)
                    ON CONFLICT (singleton_key) DO UPDATE
                    SET is_scanning_enabled = EXCLUDED.is_scanning_enabled
                    """
                )
            )

        evidence = await load_meta_snapshot_freshness(
            pg_engine,
            fb_ad_id=fb_ad_id,
            now=checked_at,
        )
        assert evidence.fresh is True
        assert evidence.scan_id == scan_id
        assert evidence.decision_confirmed is True

        async with pg_engine.begin() as conn:
            await conn.execute(
                text("UPDATE scan_runs SET outcome = 'partial' WHERE scan_id = :scan_id"),
                {"scan_id": scan_id},
            )
        assert not (
            await load_meta_snapshot_freshness(pg_engine, fb_ad_id=fb_ad_id, now=checked_at)
        ).fresh

        async with pg_engine.begin() as conn:
            await conn.execute(
                text("UPDATE scan_runs SET outcome = 'success' WHERE scan_id = :scan_id"),
                {"scan_id": scan_id},
            )
            await conn.execute(
                text("UPDATE ad_alert_state SET alert_state = 'normal' WHERE ad_id = :ad_id"),
                {"ad_id": ad_id},
            )
        assert not (
            await load_meta_snapshot_freshness(pg_engine, fb_ad_id=fb_ad_id, now=checked_at)
        ).fresh

        async with pg_engine.begin() as conn:
            await conn.execute(
                text("UPDATE ad_alert_state SET alert_state = 'stop_sent' WHERE ad_id = :ad_id"),
                {"ad_id": ad_id},
            )
            await conn.execute(
                text("UPDATE offer_rules SET updated_at = :updated_at WHERE offer_id = :id"),
                {"id": offer_id, "updated_at": checked_at + timedelta(seconds=1)},
            )
        assert not (
            await load_meta_snapshot_freshness(pg_engine, fb_ad_id=fb_ad_id, now=checked_at)
        ).fresh

        async with pg_engine.begin() as conn:
            await conn.execute(
                text("UPDATE offer_rules SET updated_at = :updated_at WHERE offer_id = :id"),
                {"id": offer_id, "updated_at": cycle_at - timedelta(minutes=1)},
            )
            await conn.execute(
                text(
                    "UPDATE observer_config SET is_scanning_enabled = FALSE "
                    "WHERE singleton_key = 'default'"
                )
            )
        assert not (
            await load_meta_snapshot_freshness(pg_engine, fb_ad_id=fb_ad_id, now=checked_at)
        ).fresh

        assert not (
            await load_meta_snapshot_freshness(
                pg_engine,
                fb_ad_id=fb_ad_id,
                now=cycle_at + timedelta(seconds=61),
            )
        ).fresh
    finally:
        async with pg_engine.begin() as conn:
            if config_existed:
                await conn.execute(
                    text(
                        "UPDATE observer_config SET is_scanning_enabled = :enabled "
                        "WHERE singleton_key = 'default'"
                    ),
                    {"enabled": bool(previous_scanning_enabled)},
                )
            else:
                await conn.execute(
                    text("DELETE FROM observer_config WHERE singleton_key = 'default'")
                )
            if scan_id is not None:
                await conn.execute(
                    text("DELETE FROM scan_runs WHERE scan_id = :scan_id"),
                    {"scan_id": scan_id},
                )
            await conn.execute(text("DELETE FROM offers WHERE id = :id"), {"id": offer_id})
