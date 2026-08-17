from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import apps.health_watchdog.main as watchdog
import core.meta_api.browser_readiness as readiness
import core.meta_api.client as meta_client
import core.tasks.queue as task_queue
from clients.python_grpc.v1 import meta_api_pb2


def _probe(**overrides):
    value = {
        "healthy": True,
        "browser_contract_version": 5,
        "vision_profile_id": "profile-1",
        "session_id": "session-1",
        "detail": "ok",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("probe", "state", "reason"),
    [
        (
            _probe(browser_contract_version=4),
            "incompatible",
            "browser_contract_incompatible",
        ),
        (
            _probe(vision_profile_id="profile-other"),
            "profile_mismatch",
            "vision_profile_mismatch",
        ),
        (
            _probe(session_id=""),
            "unavailable",
            "browser_session_missing",
        ),
        (
            _probe(healthy=False, detail="token_not_found"),
            "unavailable",
            "token_not_found",
        ),
        (
            _probe(healthy="true", browser_contract_version=5.0),
            "incompatible",
            "browser_contract_incompatible",
        ),
        (
            _probe(
                healthy=False,
                browser_contract_version=0,
                vision_profile_id="",
                session_id="",
                detail="circuit_open: browser unavailable",
            ),
            "unavailable",
            "circuit_open",
        ),
        (_probe(), "ready", "ready"),
    ],
)
def test_classify_browser_readiness_is_exact_and_fail_closed(
    probe,
    state,
    reason,
) -> None:
    result = readiness.classify_browser_readiness(
        probe,
        expected_profile_id="profile-1",
    )
    assert result.state == state
    assert result.reason_code == reason


def test_identity_loader_never_reads_or_decrypts_vision_token() -> None:
    source = inspect.getsource(readiness.load_vision_readiness_identity)
    assert "x_token" not in source
    assert "decrypt" not in source
    assert "id, profile_id, updated_at" in source


def test_legacy_per_claim_rpc_gate_is_removed() -> None:
    root = Path(__file__).resolve().parents[2]
    sources = (
        inspect.getsource(meta_client.MetaApiClient),
        (root / "apps/meta_api_worker/main.py").read_text(encoding="utf-8"),
        (root / "apps/campaign_creator_worker/main.py").read_text(encoding="utf-8"),
    )
    for source in sources:
        assert "require_exact_browser_contract" not in source
        assert "_require_browser_claim_readiness" not in source


def test_claim_contract_uses_db_clock_and_fresh_v5_evidence() -> None:
    sql = str(task_queue._BROWSER_READY_CLAIM_SQL)
    assert "clock_timestamp()" in sql
    assert "observed_contract_version = 5" in sql
    assert "readiness_expires_at > clock_timestamp()" in sql
    assert "vision_config_updated_at = config.updated_at" in sql
    assert "browser_maintenance" in sql
    persist_sql = inspect.getsource(readiness.persist_browser_readiness)
    assert "OR NOT EXISTS" in persist_sql
    assert "browser_gate.key = 'browser_maintenance'" in persist_sql
    assert "EXCLUDED.observed_at" in persist_sql
    assert "> browser_channel_readiness.observed_at" in persist_sql
    generic_sql = str(task_queue._CLAIM_SQL)
    assert "lease_expires_at =\n          clock_timestamp()" in generic_sql
    assert "available_at <= clock_timestamp()" in generic_sql
    assert "deadline_at > clock_timestamp()" in generic_sql


def test_database_trigger_is_the_single_maintenance_invalidation_path() -> None:
    root = Path(__file__).resolve().parents[2]
    baseline = (root / "migrations/versions/0001_safety_first_baseline.sql").read_text(
        encoding="utf-8"
    )
    retired_shell = root / "scripts/browser-maintenance-lease.sh"
    deployment = (root / "fbctl/controller.py").read_text(encoding="utf-8")
    python_source = (root / "core/tasks/browser_fence.py").read_text(encoding="utf-8")
    assert "invalidate_browser_readiness_on_maintenance" in baseline
    assert "trg_system_config_browser_maintenance_readiness" in baseline
    assert "UPDATE public.browser_channel_readiness" in baseline
    assert "readiness_expires_at = NULL" in baseline
    assert not retired_shell.exists()
    assert "UPDATE browser_channel_readiness" not in deployment
    assert "UPDATE browser_channel_readiness" not in python_source


@pytest.mark.parametrize(
    ("interval", "ttl"),
    [
        (0.9, 6),
        (2, 2),
        (29, 31),
        (float("inf"), 6),
        (float("nan"), 6),
    ],
)
def test_browser_readiness_schedule_fails_fast_on_invalid_pair(
    interval: float,
    ttl: int,
) -> None:
    with pytest.raises(RuntimeError, match="interval_seconds < ttl_seconds"):
        watchdog._validated_browser_readiness_schedule(interval, ttl)


def test_browser_readiness_schedule_accepts_bounded_default() -> None:
    assert watchdog._validated_browser_readiness_schedule(2, 6) == (2.0, 6)


@pytest.mark.asyncio
async def test_readiness_loop_runs_immediately_without_startup_grace(
    monkeypatch,
) -> None:
    stop = asyncio.Event()
    publish = MagicMock()

    async def _publish(*_args, **_kwargs):
        publish(*_args, **_kwargs)
        stop.set()
        return True

    monkeypatch.setattr(
        watchdog,
        "probe_and_publish_browser_readiness",
        _publish,
    )
    await watchdog.browser_readiness_loop(
        SimpleNamespace(),
        stop=stop,
        engine=SimpleNamespace(),
        interval=60,
        ttl_seconds=6,
    )
    publish.assert_called_once()


def test_check_health_contract_carries_explicit_cabinet() -> None:
    """Проба готовности обязана называть кабинет явно, а не наследовать вкладку.

    Инцидент 17.08.2026: кабинет брался из адреса текущей вкладки, и проба
    каждые 2 секунды воскрешала вкладку кабинета, которого нет ни в одном оффере.
    """
    protocol_params = inspect.signature(
        readiness.BrowserReadinessProbeClient.check_health
    ).parameters
    client_params = inspect.signature(meta_client.MetaApiClient.check_health).parameters

    assert "ad_account_id" in protocol_params
    assert "ad_account_id" in client_params
    request = meta_api_pb2.CheckMetaApiHealthRequest(ad_account_id="2108857220005012")
    assert request.ad_account_id == "2108857220005012"
