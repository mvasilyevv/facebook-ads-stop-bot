"""Residue checks for authority-bearing integration fixtures."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from tests.integration.conftest import (
    _fb_ad_fixture_scope,
    _fresh_browser_readiness_scope,
    _restore_browser_readiness_state,
    _snapshot_browser_readiness_state,
)


async def _seed_readiness_sentinel(pg_engine) -> None:
    vision_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM browser_channel_readiness WHERE channel = 'meta_api'"))
        await conn.execute(text("DELETE FROM vision_config WHERE singleton_key = 'default'"))
        updated_at = (
            await conn.execute(
                text(
                    """
                    INSERT INTO vision_config
                        (id, singleton_key, x_token_encrypted, profile_id)
                    VALUES
                        (:id, 'default', 'sentinel-token', 'sentinel-profile')
                    RETURNING updated_at
                    """
                ),
                {"id": vision_id},
            )
        ).scalar_one()
        await conn.execute(
            text(
                """
                INSERT INTO browser_channel_readiness
                    (channel, vision_config_id, vision_config_updated_at,
                     expected_profile_id, observed_profile_id,
                     observed_session_id, observed_contract_version,
                     state, reason_code, observed_at, readiness_expires_at,
                     writer_instance, generation, last_ready_at)
                VALUES
                    ('meta_api', :vision_config_id, :vision_config_updated_at,
                     'sentinel-profile', NULL, NULL, NULL,
                     'unavailable', 'sentinel-unavailable', clock_timestamp(), NULL,
                     :writer_instance, 77, NULL)
                """
            ),
            {
                "vision_config_id": vision_id,
                "vision_config_updated_at": updated_at,
                "writer_instance": uuid.uuid4(),
            },
        )


@pytest.mark.asyncio
async def test_fresh_browser_readiness_restores_exact_prior_state(pg_engine) -> None:
    outer = await _snapshot_browser_readiness_state(pg_engine)
    try:
        await _seed_readiness_sentinel(pg_engine)
        sentinel = await _snapshot_browser_readiness_state(pg_engine)

        async with _fresh_browser_readiness_scope(pg_engine):
            active = await _snapshot_browser_readiness_state(pg_engine)
            assert active.channel_readiness is not None
            assert active.channel_readiness["state"] == "ready"

        assert await _snapshot_browser_readiness_state(pg_engine) == sentinel
    finally:
        await _restore_browser_readiness_state(pg_engine, outer)


@pytest.mark.asyncio
async def test_fresh_browser_readiness_restores_after_setup_failure(
    pg_engine,
    monkeypatch,
) -> None:
    from core.meta_api import browser_readiness

    outer = await _snapshot_browser_readiness_state(pg_engine)
    try:
        await _seed_readiness_sentinel(pg_engine)
        sentinel = await _snapshot_browser_readiness_state(pg_engine)
        monkeypatch.setattr(
            browser_readiness,
            "persist_browser_readiness",
            AsyncMock(side_effect=RuntimeError("simulated readiness setup failure")),
        )

        with pytest.raises(RuntimeError, match="simulated readiness setup failure"):
            async with _fresh_browser_readiness_scope(pg_engine):
                pass

        assert await _snapshot_browser_readiness_state(pg_engine) == sentinel
    finally:
        await _restore_browser_readiness_state(pg_engine, outer)


@pytest.mark.asyncio
async def test_fb_ad_fixture_scope_deletes_exact_chain_after_body_failure(pg_engine) -> None:
    resource = None
    with pytest.raises(RuntimeError, match="simulated test body failure"):
        async with _fb_ad_fixture_scope(pg_engine) as created:
            resource = created
            raise RuntimeError("simulated test body failure")

    assert resource is not None
    async with pg_engine.connect() as conn:
        counts = {
            "ad": await conn.scalar(
                text("SELECT COUNT(*) FROM fb_ads WHERE id = :id"),
                {"id": resource.ad_id},
            ),
            "adset": await conn.scalar(
                text("SELECT COUNT(*) FROM fb_adsets WHERE id = :id"),
                {"id": resource.adset_id},
            ),
            "campaign": await conn.scalar(
                text("SELECT COUNT(*) FROM fb_campaigns WHERE id = :id"),
                {"id": resource.campaign_id},
            ),
            "offer": await conn.scalar(
                text("SELECT COUNT(*) FROM offers WHERE id = :id"),
                {"id": resource.offer_id},
            ),
        }
    assert counts == {"ad": 0, "adset": 0, "campaign": 0, "offer": 0}
