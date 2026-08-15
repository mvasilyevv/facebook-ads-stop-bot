"""Python/browser-agent contract for fenced money RPC capabilities."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from clients.python_grpc.v1 import meta_api_pb2
from core.campaign_builder import (
    Account,
    Budget,
    CampaignConfig,
    Targeting,
    ad_body,
    adset_body,
    campaign_body,
    image_creative_body,
    video_creative_body,
)
from core.meta_api.client import (
    BROWSER_CONTRACT_VERSION,
    DUPLICATE_PROVE_AD_FIELDS,
    DUPLICATE_PROVE_ADSET_FIELDS,
    DUPLICATE_PROVE_CAMPAIGN_FIELDS,
    DUPLICATE_SOURCE_AD_FIELDS,
    DUPLICATE_SOURCE_ADSET_FIELDS,
    DUPLICATE_SOURCE_CAMPAIGN_FIELDS,
    DUPLICATE_VERIFY_AD_FIELDS,
    MetaApiClient,
    graph_operation_binding,
    media_operation_binding,
)
from core.meta_api.errors import (
    AmbiguousResultError,
    PermanentError,
    SessionUnavailableError,
)

_SECRET = "browser-operation-test-secret-" + ("s" * 48)
_STATUS_OPERATION = graph_operation_binding(
    method="POST",
    endpoint="/987654321",
    query_params={"status": "PAUSED"},
    body_json="",
)
_STATUS_GRAPH_SEMANTICS = {
    "graph_method": "POST",
    "graph_endpoint": "/987654321",
    "graph_query_params": {"status": "PAUSED"},
    "graph_body_json": "",
}
_VIDEO_OPERATION = media_operation_binding(
    rpc="upload_video",
    attributes={
        "filename": "creative.mp4",
        "file_size": 5,
        "content_sha256": hashlib.sha256(b"video").hexdigest(),
    },
)


def test_python_request_binding_matches_cross_runtime_canonical_vector() -> None:
    assert graph_operation_binding(
        method="post",
        endpoint="/987654321",
        query_params={"status": "PAUSED", "batch": "[]"},
        body_json='{"value":1}',
    ) == (
        "POST:/987654321"
        "|q=96ba85c6a27e6cfada78a34fd6937ad0132bfd46dfed0469c52a1012b0b601eb"
        "|b=48208f9428d64634bd8e28ff345bf0eab60d53c18fa2fbdb0b9bc1e84df2b5f6"
    )
    assert media_operation_binding(
        rpc="upload_image",
        attributes={
            "filename": "hero.jpg",
            "content_type": "image/jpeg",
            "content_sha256": "a" * 64,
        },
    ) == ("upload_image|r=ef5196641216c0bb73b2dd598de83aeb5483bc079adcd952810923ad6815649f")


def _operation_engine(row: dict[str, object] | None) -> MagicMock:
    timeout_result = MagicMock()
    live_result = MagicMock()
    live_result.mappings.return_value.one_or_none.return_value = row
    insert_result = MagicMock()
    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=[timeout_result, live_result, insert_result])
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    engine._test_connection = connection
    return engine


def _repeating_operation_engine(row: dict[str, object]) -> MagicMock:
    timeout_result = MagicMock()
    live_result = MagicMock()
    live_result.mappings.return_value.one_or_none.return_value = row
    insert_result = MagicMock()

    async def execute(statement: object, _params: object) -> MagicMock:
        sql = str(statement)
        if "FROM task_queue AS tq" in sql:
            return live_result
        if "INSERT INTO browser_operation_capability_uses" in sql:
            return insert_result
        return timeout_result

    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=execute)
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    engine._test_connection = connection
    return engine


def _live_row(
    *,
    caller: str = "autopause",
    lease_expires_epoch: int = 1_800_000_030,
    result: dict[str, object] | None = None,
) -> dict[str, object]:
    if caller == "campaign_creator":
        return {
            "task_type": "campaign_create",
            "lane": "bulk",
            "requested_by": "operator:web",
            "payload": {"run_id": "864f701e-f008-4de7-b677-18a879f1f260"},
            "db_now_epoch": 1_800_000_000,
            "lease_expires_epoch": lease_expires_epoch,
            "deadline_epoch": lease_expires_epoch + 60,
            "bound_ad_account_id": "act_123",
        }
    row: dict[str, object] = {
        "task_type": "meta_api_mutation",
        "lane": "money" if caller == "autopause" else "bulk",
        "requested_by": "bot_auto_stop" if caller == "autopause" else "operator:web",
        "payload": {
            "mutation_kind": ("pause_ad" if caller == "autopause" else "duplicate_adset_structure"),
            "target_id": "987654321",
            "ad_account_id": "123",
            **(
                {
                    "params": {
                        "source_campaign_id": "111",
                        "source_adset_id": "987654321",
                        "selected_ad_ids": ["301"],
                        "campaign_count": 1,
                        "adsets_per_campaign": 1,
                        "budget_level": "ABO",
                        "daily_budget": "50.00",
                        "currency": "USD",
                        "currency_exponent": 2,
                        "start_time": "2026-08-01T00:00:00Z",
                        "campaign_names": ["Campaign 1"],
                        "adset_names": [["Ad set 1"]],
                    }
                }
                if caller == "meta_api"
                else {}
            ),
        },
        "db_now_epoch": 1_800_000_000,
        "lease_expires_epoch": lease_expires_epoch,
        "deadline_epoch": lease_expires_epoch + 60,
        "bound_ad_account_id": "123",
    }
    if result is not None:
        row["result"] = result
    return row


def _graph_response(value: dict[str, object]) -> meta_api_pb2.ExecuteGraphCallResponse:
    return meta_api_pb2.ExecuteGraphCallResponse(
        status_code=200,
        response_json=json.dumps(value),
    )


def _duplicate_client(
    *,
    row: dict[str, object] | None = None,
    responses: list[dict[str, object]] | None = None,
) -> tuple[MetaApiClient, MagicMock, MagicMock]:
    engine = _repeating_operation_engine(row or _live_row(caller="meta_api"))
    breaker = MagicMock()
    breaker.call = AsyncMock(side_effect=[_graph_response(value) for value in (responses or [])])
    client = MetaApiClient(
        session_id="session-exact",
        circuit_breaker=breaker,
        operation_engine=engine,
    )
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        ),
        ExecuteGraphCallV5=AsyncMock(),
    )
    return client, engine, breaker


def _duplicate_source_responses() -> list[dict[str, object]]:
    return [
        {
            "id": "111",
            "account_id": "123",
            "objective": "OUTCOME_SALES",
            "special_ad_categories": ["NONE"],
        },
        {
            "id": "987654321",
            "account_id": "123",
            "campaign_id": "111",
        },
        {
            "id": "301",
            "account_id": "123",
            "campaign_id": "111",
            "adset_id": "987654321",
            "name": "Source ad",
            "creative": {"id": "401"},
        },
    ]


async def _load_duplicate_sources(client: MetaApiClient) -> None:
    await client.execute_graph_call(
        method="GET",
        endpoint="/111",
        query_params={"fields": DUPLICATE_SOURCE_CAMPAIGN_FIELDS},
        ad_account_id="123",
    )
    await client.execute_graph_call(
        method="GET",
        endpoint="/987654321",
        query_params={"fields": DUPLICATE_SOURCE_ADSET_FIELDS},
        ad_account_id="123",
    )
    await client.execute_graph_call(
        method="GET",
        endpoint="/301",
        query_params={"fields": DUPLICATE_SOURCE_AD_FIELDS},
        ad_account_id="123",
    )


async def _create_duplicate_campaign(client: MetaApiClient) -> dict[str, object]:
    return await client.execute_graph_call(
        method="POST",
        endpoint="/act_123/campaigns",
        query_params={},
        body_json={
            "name": "Campaign 1",
            "objective": "OUTCOME_SALES",
            "special_ad_categories": ["NONE"],
            "status": "PAUSED",
        },
        ad_account_id="123",
    )


def _duplicate_grant_count(engine: MagicMock) -> int:
    return sum(
        "INSERT INTO browser_operation_capability_uses" in str(call.args[0])
        for call in engine._test_connection.execute.await_args_list
    )


def _campaign_client() -> tuple[MetaApiClient, MagicMock]:
    engine = _operation_engine(_live_row(caller="campaign_creator"))
    client = MetaApiClient(
        session_id="session-exact",
        operation_engine=engine,
    )
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )
    return client, engine


def _valid_campaign_create_body(edge: str) -> dict[str, object]:
    if edge == "campaigns":
        return {
            "name": "Campaign",
            "objective": "OUTCOME_SALES",
            "status": "PAUSED",
            "special_ad_categories": ["NONE"],
        }
    if edge == "adsets":
        return {
            "name": "Ad set",
            "billing_event": "IMPRESSIONS",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "destination_type": "WEBSITE",
            "promoted_object": {
                "pixel_id": "201",
                "custom_event_type": "PURCHASE",
                "smart_pse_enabled": False,
            },
            "attribution_spec": [
                {"event_type": "CLICK_THROUGH", "window_days": 1},
                {"event_type": "VIEW_THROUGH", "window_days": 1},
            ],
            "targeting": {
                "geo_locations": {
                    "countries": ["US", "AQ"],
                    "location_types": ["home", "recent"],
                },
                "age_min": 21,
                "age_max": 65,
                "targeting_automation": {"advantage_audience": 1},
            },
            "start_time": "2026-08-01T00:00:00+0000",
            "status": "PAUSED",
            "campaign_id": "101",
        }
    if edge == "adcreatives":
        return {
            "name": "Creative",
            "object_story_spec": {
                "page_id": "201",
                "link_data": {
                    "link": "https://example.test/offer",
                    "call_to_action": {
                        "type": "PLAY_GAME",
                        "value": {"link": "https://example.test/offer"},
                    },
                    "image_hash": "image-hash-1",
                },
            },
            "url_tags": "sub8={{ad.id}}",
            "degrees_of_freedom_spec": {
                "creative_features_spec": {"text_optimizations": {"enroll_status": "OPT_OUT"}}
            },
        }
    if edge == "ads":
        return {
            "name": "Ad",
            "adset_id": "301",
            "creative": {"creative_id": "401"},
            "status": "PAUSED",
        }
    raise AssertionError(f"unexpected campaign edge {edge!r}")


def _builder_config(*, budget_level: str) -> CampaignConfig:
    return CampaignConfig(
        account=Account(
            act_id="123",
            page_id="201",
            pixel_id="202",
            timezone_name="America/New_York",
            currency="USD",
            account_context_observed_at="2026-07-29T12:00:00+00:00",
        ),
        offer_code="OFFER",
        destination_link="https://example.test/offer",
        start_date="2026-07-30",
        budget=Budget(
            level=budget_level,
            currency="USD",
            daily_amount="3.00",
            bid_strategy="COST_CAP",
            bid_amount="5.00",
        ),
        targeting=Targeting(countries=["US"]),
        campaigns=[],
    )


def _builder_create_cases() -> list[tuple[str, dict[str, object], str]]:
    cbo = _builder_config(budget_level="campaign")
    abo = _builder_config(budget_level="adset")
    adset_cbo = adset_body(cbo, "CBO ad set")
    adset_cbo["campaign_id"] = "101"
    adset_abo = adset_body(abo, "ABO ad set")
    adset_abo["campaign_id"] = "101"
    return [
        ("campaigns", campaign_body(cbo, "CBO campaign"), "none"),
        ("campaigns", campaign_body(abo, "ABO campaign"), "none"),
        ("adsets", adset_cbo, "campaign"),
        ("adsets", adset_abo, "campaign"),
        (
            "adcreatives",
            image_creative_body(
                cbo,
                "Image creative",
                "image-hash-1",
                "sub8={{ad.id}}",
            ),
            "image",
        ),
        (
            "adcreatives",
            video_creative_body(
                cbo,
                "Video creative",
                "501",
                "https://example.test/thumbnail.jpg",
                "sub8={{ad.id}}",
            ),
            "video",
        ),
        ("ads", ad_body("Ad", "301", "401"), "ad"),
    ]


async def _prepare_campaign_graph(
    client: MetaApiClient,
    *,
    method: str,
    endpoint: str,
    query_params: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    query = query_params or {}
    body_json = json.dumps(body) if body is not None else ""
    return await client.prepare_operation_authorization(
        rpc="execute_graph_call",
        operation=graph_operation_binding(
            method=method,
            endpoint=endpoint,
            query_params=query,
            body_json=body_json,
        ),
        ad_account_id="123",
        graph_method=method,
        graph_endpoint=endpoint,
        graph_query_params=query,
        graph_body_json=body_json,
    )


def test_generated_python_stubs_expose_every_fenced_capability_field() -> None:
    expected = {
        "vision_profile_id",
        "authorized_caller",
        "task_id",
        "lease_owner",
        "lease_token",
        "capability_expires_at",
        "capability_nonce",
        "capability_signature",
    }
    for message in (
        meta_api_pb2.ExecuteGraphCallRequest,
        meta_api_pb2.UploadImageRequest,
        meta_api_pb2.UploadVideoChunk,
    ):
        assert expected <= set(message.DESCRIPTOR.fields_by_name)

    request = meta_api_pb2.ExecuteGraphCallRequest(
        session_id="session-exact",
        vision_profile_id="profile-exact",
        ad_account_id="123",
        authorized_caller="autopause",
        task_id=1842,
        lease_owner="2c5114e4-d921-4fc5-9986-18831eb56d5d",
        lease_token=7,
        capability_expires_at=1_800_000_030,
        capability_nonce="a" * 32,
        capability_signature="b" * 64,
    )
    assert request.task_id == 1842
    assert request.vision_profile_id == "profile-exact"


@pytest.mark.asyncio
async def test_python_client_signs_exact_live_identity_task_and_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    monkeypatch.setattr("core.meta_api.client.time.time", lambda: 1_800_000_000)
    monkeypatch.setattr("core.meta_api.client.secrets.token_hex", lambda _size: "c" * 32)
    health = AsyncMock(
        return_value=SimpleNamespace(
            healthy=True,
            browser_contract_version=BROWSER_CONTRACT_VERSION,
            session_id="session-exact",
            vision_profile_id="profile-exact",
        )
    )
    engine = _operation_engine(_live_row())
    client = MetaApiClient(session_id="session-exact", operation_engine=engine)
    client._stub = SimpleNamespace(CheckMetaApiHealth=health)
    lease_owner = uuid.UUID("2c5114e4-d921-4fc5-9986-18831eb56d5d")

    with client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=lease_owner,
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        authorization = await client.prepare_operation_authorization(
            rpc="execute_graph_call",
            operation=_STATUS_OPERATION,
            ad_account_id="act_123",
            **_STATUS_GRAPH_SEMANTICS,
        )

    payload = "\n".join(
        (
            "browser_operation/v2",
            str(BROWSER_CONTRACT_VERSION),
            "execute_graph_call",
            _STATUS_OPERATION,
            "session-exact",
            "profile-exact",
            "123",
            "autopause",
            "1842",
            str(lease_owner),
            "7",
            "1800000030",
            "c" * 32,
        )
    )
    expected_signature = hmac.new(
        _SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    assert authorization == {
        "session_id": "session-exact",
        "vision_profile_id": "profile-exact",
        "authorized_caller": "autopause",
        "task_id": 1842,
        "lease_owner": str(lease_owner),
        "lease_token": 7,
        "capability_expires_at": 1_800_000_030,
        "capability_nonce": "c" * 32,
        "capability_signature": expected_signature,
    }
    request = health.await_args.args[0]
    assert request.session_id == "session-exact"
    assert request.expected_vision_profile_id == "profile-exact"
    assert engine._test_connection.execute.await_count == 3
    db_params = engine._test_connection.execute.await_args_list[1].args[1]
    assert db_params["task_id"] == 1842
    assert db_params["lease_owner"] == lease_owner
    assert db_params["lease_token"] == 7
    pending_params = engine._test_connection.execute.await_args_list[2].args[1]
    assert pending_params["task_id"] == 1842
    assert pending_params["capability_digest"] == hashlib.sha256(payload.encode()).digest()
    assert pending_params["operation_digest"] == hashlib.sha256(_STATUS_OPERATION.encode()).digest()
    assert pending_params["browser_contract_version"] == BROWSER_CONTRACT_VERSION


@pytest.mark.asyncio
async def test_python_client_fails_closed_without_task_authority_or_exact_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client = MetaApiClient(
        session_id="session-exact",
        operation_engine=_operation_engine(_live_row()),
    )
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="wrong-profile",
            )
        )
    )

    with pytest.raises(PermanentError, match="claimed-task"):
        await client.prepare_operation_authorization(
            rpc="execute_graph_call",
            operation=_STATUS_OPERATION,
            ad_account_id="123",
            **_STATUS_GRAPH_SEMANTICS,
        )

    with client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(SessionUnavailableError, match="not ready"):
            await client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=_STATUS_OPERATION,
                ad_account_id="123",
                **_STATUS_GRAPH_SEMANTICS,
            )


@pytest.mark.asyncio
async def test_python_client_rejects_stale_browser_contract_before_db_or_browser_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    engine = _operation_engine(_live_row())
    health = AsyncMock(
        return_value=SimpleNamespace(
            healthy=True,
            browser_contract_version=BROWSER_CONTRACT_VERSION - 1,
            session_id="session-exact",
            vision_profile_id="profile-exact",
        )
    )
    client = MetaApiClient(session_id="session-exact", operation_engine=engine)
    client._stub = SimpleNamespace(CheckMetaApiHealth=health)

    with client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(
            SessionUnavailableError,
            match=(
                rf"required={BROWSER_CONTRACT_VERSION}, "
                rf"observed={BROWSER_CONTRACT_VERSION - 1}"
            ),
        ):
            await client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=_STATUS_OPERATION,
                ad_account_id="123",
                **_STATUS_GRAPH_SEMANTICS,
            )

    health.assert_awaited_once()
    engine._test_connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_issuer_rejects_opaque_digest_without_canonical_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    health = AsyncMock(
        return_value=SimpleNamespace(
            healthy=True,
            browser_contract_version=BROWSER_CONTRACT_VERSION,
            session_id="session-exact",
            vision_profile_id="profile-exact",
        )
    )
    engine = _operation_engine(_live_row())
    client = MetaApiClient(
        session_id="session-exact",
        operation_engine=engine,
    )
    client._stub = SimpleNamespace(CheckMetaApiHealth=health)

    with client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="canonical request semantics"):
            await client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=_STATUS_OPERATION,
                ad_account_id="123",
            )

    health.assert_not_awaited()
    engine._test_connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_python_client_rejects_stale_or_mismatched_live_task_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    health = AsyncMock(
        return_value=SimpleNamespace(
            healthy=True,
            browser_contract_version=BROWSER_CONTRACT_VERSION,
            session_id="session-exact",
            vision_profile_id="profile-exact",
        )
    )
    lease_owner = uuid.uuid4()

    stale_client = MetaApiClient(
        session_id="session-exact",
        operation_engine=_operation_engine(None),
    )
    stale_client._stub = SimpleNamespace(CheckMetaApiHealth=health)
    with stale_client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=lease_owner,
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(SessionUnavailableError, match="stale, cancelled, expired"):
            await stale_client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=_STATUS_OPERATION,
                ad_account_id="123",
                **_STATUS_GRAPH_SEMANTICS,
            )

    wrong_lane = _live_row()
    wrong_lane["lane"] = "bulk"
    mismatched_client = MetaApiClient(
        session_id="session-exact",
        operation_engine=_operation_engine(wrong_lane),
    )
    mismatched_client._stub = SimpleNamespace(CheckMetaApiHealth=health)
    with mismatched_client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=lease_owner,
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="caller/task/lane"):
            await mismatched_client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=_STATUS_OPERATION,
                ad_account_id="123",
                **_STATUS_GRAPH_SEMANTICS,
            )


@pytest.mark.asyncio
async def test_python_client_fails_closed_without_absolute_task_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    row = _live_row()
    row["deadline_epoch"] = None
    client = MetaApiClient(
        session_id="session-exact",
        operation_engine=_operation_engine(row),
    )
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )

    with client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(SessionUnavailableError, match="absolute deadline"):
            await client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=_STATUS_OPERATION,
                ad_account_id="123",
                **_STATUS_GRAPH_SEMANTICS,
            )


@pytest.mark.asyncio
async def test_python_client_wraps_authority_database_failure_before_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(
        side_effect=RuntimeError("database unavailable")
    )
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    client = MetaApiClient(session_id="session-exact", operation_engine=engine)
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )

    with client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(SessionUnavailableError, match="before browser send") as caught:
            await client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=_STATUS_OPERATION,
                ad_account_id="123",
                **_STATUS_GRAPH_SEMANTICS,
            )

    assert isinstance(caught.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_python_client_bounds_authority_database_read_before_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    monkeypatch.setattr(
        "core.meta_api.client._OPERATION_AUTHORITY_DB_TIMEOUT_SECONDS",
        0.01,
    )

    async def _blocked_connect():
        await asyncio.sleep(60)

    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(side_effect=_blocked_connect)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    client = MetaApiClient(session_id="session-exact", operation_engine=engine)
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )

    with client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(SessionUnavailableError, match="before browser send") as caught:
            await client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=_STATUS_OPERATION,
                ad_account_id="123",
                **_STATUS_GRAPH_SEMANTICS,
            )

    assert isinstance(caught.value.__cause__, TimeoutError)


@pytest.mark.asyncio
async def test_upload_capability_covers_rpc_deadline_but_is_lease_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    monkeypatch.setattr("core.meta_api.client.time.time", lambda: 1_800_000_000)
    monkeypatch.setattr("core.meta_api.client.secrets.token_hex", lambda _size: "d" * 32)
    client = MetaApiClient(
        session_id="session-exact",
        operation_engine=_operation_engine(
            _live_row(
                caller="campaign_creator",
                lease_expires_epoch=1_800_000_300,
            )
        ),
    )
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )

    with client.operation_authority(
        caller="campaign_creator",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        authorization = await client.prepare_operation_authorization(
            rpc="upload_video",
            operation=_VIDEO_OPERATION,
            ad_account_id="123",
        )

    assert authorization["capability_expires_at"] == 1_800_000_185


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "edge",
    ["campaigns", "adsets", "adcreatives", "ads"],
)
async def test_campaign_creator_issues_only_intended_paused_create_edges(
    monkeypatch: pytest.MonkeyPatch,
    edge: str,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine = _campaign_client()

    with client.operation_authority(
        caller="campaign_creator",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        if edge == "adsets":
            client._remember_campaign_created_object_id(
                endpoint="/act_123/campaigns",
                object_id="101",
                ad_account_id="123",
            )
        elif edge == "adcreatives":
            client._remember_campaign_uploaded_image_hash(
                "image-hash-1",
                ad_account_id="123",
            )
        elif edge == "ads":
            client._remember_campaign_created_object_id(
                endpoint="/act_123/adsets",
                object_id="301",
                ad_account_id="123",
            )
            client._remember_campaign_created_object_id(
                endpoint="/act_123/adcreatives",
                object_id="401",
                ad_account_id="123",
            )
        authorization = await _prepare_campaign_graph(
            client,
            method="POST",
            endpoint=f"/act_123/{edge}",
            body=_valid_campaign_create_body(edge),
        )

    assert authorization["authorized_caller"] == "campaign_creator"
    assert engine._test_connection.execute.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("edge", "body", "provenance"),
    _builder_create_cases(),
    ids=[
        "campaign-cbo",
        "campaign-abo",
        "adset-cbo",
        "adset-abo",
        "creative-image",
        "creative-video",
        "ad",
    ],
)
async def test_campaign_creator_accepts_exact_current_builder_bodies(
    monkeypatch: pytest.MonkeyPatch,
    edge: str,
    body: dict[str, object],
    provenance: str,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine = _campaign_client()

    with client.operation_authority(
        caller="campaign_creator",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        if provenance == "campaign":
            client._remember_campaign_created_object_id(
                endpoint="/act_123/campaigns",
                object_id="101",
                ad_account_id="123",
            )
        elif provenance == "image":
            client._remember_campaign_uploaded_image_hash(
                "image-hash-1",
                ad_account_id="123",
            )
        elif provenance == "video":
            client._remember_campaign_uploaded_video_id(
                "501",
                ad_account_id="123",
            )
        elif provenance == "ad":
            client._remember_campaign_created_object_id(
                endpoint="/act_123/adsets",
                object_id="301",
                ad_account_id="123",
            )
            client._remember_campaign_created_object_id(
                endpoint="/act_123/adcreatives",
                object_id="401",
                ad_account_id="123",
            )
        authorization = await _prepare_campaign_graph(
            client,
            method="POST",
            endpoint=f"/act_123/{edge}",
            body=body,
        )

    assert authorization["authorized_caller"] == "campaign_creator"
    assert engine._test_connection.execute.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("targeting_patch", "field"),
    [
        ({"genders": [2]}, "genders"),
        ({"publisher_platforms": ["facebook"]}, "publisher_platforms"),
    ],
)
async def test_campaign_creator_documents_manual_targeting_capability_loss(
    monkeypatch: pytest.MonkeyPatch,
    targeting_patch: dict[str, object],
    field: str,
) -> None:
    """The builder emits these wizard choices, but the client rejects them pre-send."""

    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, _engine = _campaign_client()
    body = adset_body(_builder_config(budget_level="campaign"), "Ad set")
    body["campaign_id"] = "101"
    body["targeting"].update(targeting_patch)

    with client.operation_authority(
        caller="campaign_creator",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        client._remember_campaign_created_object_id(
            endpoint="/act_123/campaigns",
            object_id="101",
            ad_account_id="123",
        )
        with pytest.raises(PermanentError, match="targeting schema is not authorized"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/act_123/adsets",
                body=body,
            )

    assert field in body["targeting"]


@pytest.mark.asyncio
async def test_campaign_create_ack_records_task_local_object_provenance() -> None:
    breaker = MagicMock()
    breaker.call = AsyncMock(
        return_value=meta_api_pb2.ExecuteGraphCallResponse(
            status_code=200,
            response_json='{"id":"101"}',
        )
    )
    client = MetaApiClient(
        session_id="session-exact",
        circuit_breaker=breaker,
    )
    client._stub = SimpleNamespace(ExecuteGraphCallV5=AsyncMock())
    client.prepare_operation_authorization = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "session_id": "session-exact",
            "vision_profile_id": "profile-exact",
            "authorized_caller": "campaign_creator",
            "task_id": 1842,
            "lease_owner": "2c5114e4-d921-4fc5-9986-18831eb56d5d",
            "lease_token": 7,
            "capability_expires_at": 1_800_000_030,
            "capability_nonce": "a" * 32,
            "capability_signature": "b" * 64,
        }
    )
    remember = MagicMock(wraps=client._remember_campaign_created_object_id)
    client._remember_campaign_created_object_id = remember  # type: ignore[method-assign]

    with client.operation_authority(
        caller="campaign_creator",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        result = await client.execute_graph_call(
            method="POST",
            endpoint="/act_123/campaigns",
            body_json=_valid_campaign_create_body("campaigns"),
            ad_account_id="123",
        )

    assert result == {"id": "101"}
    remember.assert_called_once_with(
        endpoint="/act_123/campaigns",
        object_id="101",
        ad_account_id="123",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "endpoint", "query_params", "body", "remember_video"),
    [
        ("POST", "/999", {}, {"status": "PAUSED"}, False),
        (
            "POST",
            "/act_123/customaudiences",
            {},
            {"name": "unexpected"},
            False,
        ),
        (
            "POST",
            "/act_123/campaigns",
            {},
            {
                **_valid_campaign_create_body("campaigns"),
                "status": "ACTIVE",
            },
            False,
        ),
        (
            "POST",
            "/act_123/campaigns",
            {},
            {
                **_valid_campaign_create_body("campaigns"),
                "configured_status": "PAUSED",
            },
            False,
        ),
        (
            "POST",
            "/act_123/adsets",
            {},
            _valid_campaign_create_body("adsets"),
            False,
        ),
        (
            "POST",
            "/act_123/adcreatives",
            {},
            _valid_campaign_create_body("adcreatives"),
            False,
        ),
        (
            "POST",
            "/act_123/ads",
            {},
            _valid_campaign_create_body("ads"),
            False,
        ),
        (
            "POST",
            "/act_123/ads",
            {"published": "true"},
            {"name": "Ad", "status": "PAUSED"},
            False,
        ),
        ("GET", "/999", {"fields": "status"}, None, False),
        ("GET", "/999", {"fields": "status", "limit": "1"}, None, True),
        ("GET", "/999/thumbnails", {"fields": "uri"}, None, True),
        ("GET", "/act_123/campaigns", {}, None, False),
    ],
)
async def test_campaign_creator_rejects_arbitrary_graph_actions_before_grant_insert(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    endpoint: str,
    query_params: dict[str, str],
    body: dict[str, object] | None,
    remember_video: bool,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine = _campaign_client()

    with client.operation_authority(
        caller="campaign_creator",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        if remember_video:
            client._remember_campaign_uploaded_video_id(
                "999",
                ad_account_id="123",
            )
        with pytest.raises(PermanentError, match="campaign Graph"):
            await _prepare_campaign_graph(
                client,
                method=method,
                endpoint=endpoint,
                query_params=query_params,
                body=body,
            )

    # statement timeout + live task read happened; no pending grant was inserted.
    assert engine._test_connection.execute.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("edge", "body", "second_task_seed"),
    [
        ("adsets", _valid_campaign_create_body("adsets"), "none"),
        ("ads", _valid_campaign_create_body("ads"), "none"),
        ("ads", _valid_campaign_create_body("ads"), "adset"),
    ],
    ids=["campaign-id", "adset-id", "creative-id"],
)
async def test_campaign_object_ids_do_not_cross_task_authority_contexts(
    monkeypatch: pytest.MonkeyPatch,
    edge: str,
    body: dict[str, object],
    second_task_seed: str,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine = _campaign_client()

    with client.operation_authority(
        caller="campaign_creator",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        client._remember_campaign_created_object_id(
            endpoint="/act_123/campaigns",
            object_id="101",
            ad_account_id="123",
        )
        client._remember_campaign_created_object_id(
            endpoint="/act_123/adsets",
            object_id="301",
            ad_account_id="123",
        )
        client._remember_campaign_created_object_id(
            endpoint="/act_123/adcreatives",
            object_id="401",
            ad_account_id="123",
        )

    with client.operation_authority(
        caller="campaign_creator",
        task_id=1843,
        lease_owner=uuid.uuid4(),
        lease_token=8,
        vision_profile_id="profile-exact",
    ):
        if second_task_seed == "adset":
            client._remember_campaign_created_object_id(
                endpoint="/act_123/adsets",
                object_id="301",
                ad_account_id="123",
            )
        with pytest.raises(PermanentError, match="task-local"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint=f"/act_123/{edge}",
                body=body,
            )

    assert engine._test_connection.execute.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "query_params"),
    [
        ("/999", {"fields": "status"}),
        ("/999/thumbnails", {"fields": "uri,is_preferred"}),
    ],
)
async def test_campaign_video_reads_require_same_task_upload_provenance(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    query_params: dict[str, str],
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine = _campaign_client()

    with client.operation_authority(
        caller="campaign_creator",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        client._remember_campaign_uploaded_video_id(
            "999",
            ad_account_id="123",
        )
        authorization = await _prepare_campaign_graph(
            client,
            method="GET",
            endpoint=endpoint,
            query_params=query_params,
        )

    assert authorization["authorized_caller"] == "campaign_creator"
    assert engine._test_connection.execute.await_count == 3

    fresh_client, fresh_engine = _campaign_client()
    with fresh_client.operation_authority(
        caller="campaign_creator",
        task_id=1843,
        lease_owner=uuid.uuid4(),
        lease_token=8,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="upload provenance"):
            await _prepare_campaign_graph(
                fresh_client,
                method="GET",
                endpoint=endpoint,
                query_params=query_params,
            )
    assert fresh_engine._test_connection.execute.await_count == 2


@pytest.mark.asyncio
async def test_meta_api_duplicate_capability_enforces_exact_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine, breaker = _duplicate_client(
        responses=[
            {
                "id": "111",
                "account_id": "123",
                "objective": "OUTCOME_SALES",
                "special_ad_categories": ["NONE"],
            },
            {
                "id": "987654321",
                "account_id": "123",
                "campaign_id": "111",
            },
            {
                "id": "301",
                "account_id": "123",
                "campaign_id": "111",
                "adset_id": "987654321",
                "name": "Source ad",
                "creative": {"id": "401"},
            },
            {"id": "501"},
            {
                "id": "501",
                "account_id": "123",
                "name": "Campaign 1",
                "objective": "OUTCOME_SALES",
                "status": "PAUSED",
            },
            {"copied_adset_id": "601"},
            {
                "id": "601",
                "account_id": "123",
                "campaign_id": "501",
                "status": "PAUSED",
            },
            {"success": True},
            {"id": "701"},
            {
                "id": "701",
                "account_id": "123",
                "campaign_id": "501",
                "adset_id": "601",
                "name": "Source ad",
                "status": "PAUSED",
                "creative": {"id": "401"},
            },
            {"id": "501", "status": "PAUSED", "daily_budget": "0"},
            {
                "id": "601",
                "campaign_id": "501",
                "status": "PAUSED",
                "daily_budget": "5000",
                "lifetime_budget": "0",
                "start_time": "2026-08-01T00:00:00Z",
            },
            {
                "data": [
                    {
                        "id": "701",
                        "adset_id": "601",
                        "status": "PAUSED",
                        "effective_status": "PAUSED",
                        "creative": {"id": "401"},
                    }
                ]
            },
            {"success": True},
            {"id": "701", "status": "PAUSED", "effective_status": "PAUSED"},
        ]
    )

    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="task-local provenance"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/999999999",
                body={"status": "PAUSED"},
            )

        await client.execute_graph_call(
            method="GET",
            endpoint="/111",
            query_params={"fields": DUPLICATE_SOURCE_CAMPAIGN_FIELDS},
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="GET",
            endpoint="/987654321",
            query_params={"fields": DUPLICATE_SOURCE_ADSET_FIELDS},
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="GET",
            endpoint="/301",
            query_params={"fields": DUPLICATE_SOURCE_AD_FIELDS},
            ad_account_id="123",
        )

        with pytest.raises(PermanentError, match="campaign create body"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/act_123/campaigns",
                body={
                    "name": "Campaign 1",
                    "objective": "OUTCOME_SALES",
                    "special_ad_categories": ["NONE"],
                    "status": "ACTIVE",
                },
            )
        campaign = await client.execute_graph_call(
            method="POST",
            endpoint="/act_123/campaigns",
            query_params={},
            body_json={
                "name": "Campaign 1",
                "objective": "OUTCOME_SALES",
                "special_ad_categories": ["NONE"],
                "status": "PAUSED",
            },
            ad_account_id="123",
        )
        assert campaign["id"] == "501"
        await client.execute_graph_call(
            method="GET",
            endpoint="/501",
            query_params={"fields": DUPLICATE_PROVE_CAMPAIGN_FIELDS},
            ad_account_id="123",
        )

        with pytest.raises(PermanentError, match="adset copy body"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/987654321/copies",
                body={
                    "campaign_id": "501",
                    "deep_copy": True,
                    "status_option": "PAUSED",
                },
            )
        copied = await client.execute_graph_call(
            method="POST",
            endpoint="/987654321/copies",
            query_params={},
            body_json={
                "campaign_id": "501",
                "deep_copy": False,
                "status_option": "PAUSED",
            },
            ad_account_id="123",
        )
        assert copied["copied_adset_id"] == "601"
        await client.execute_graph_call(
            method="GET",
            endpoint="/601",
            query_params={"fields": DUPLICATE_PROVE_ADSET_FIELDS},
            ad_account_id="123",
        )

        with pytest.raises(PermanentError, match="configuration body"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/601",
                body={
                    "name": "Ad set 1",
                    "status": "ACTIVE",
                    "start_time": "2026-08-01T00:00:00Z",
                    "daily_budget": 5000,
                },
            )
        await client.execute_graph_call(
            method="POST",
            endpoint="/601",
            query_params={},
            body_json={
                "name": "Ad set 1",
                "status": "PAUSED",
                "start_time": "2026-08-01T00:00:00Z",
                "daily_budget": 5000,
            },
            ad_account_id="123",
        )

        with pytest.raises(PermanentError, match="ad create body"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/act_123/ads",
                body={
                    "name": "Source ad",
                    "adset_id": "601",
                    "creative": {"creative_id": "999"},
                    "status": "PAUSED",
                },
            )
        ad = await client.execute_graph_call(
            method="POST",
            endpoint="/act_123/ads",
            query_params={},
            body_json={
                "name": "Source ad",
                "adset_id": "601",
                "creative": {"creative_id": "401"},
                "status": "PAUSED",
            },
            ad_account_id="123",
        )
        assert ad["id"] == "701"
        await client.execute_graph_call(
            method="GET",
            endpoint="/701",
            query_params={"fields": DUPLICATE_PROVE_AD_FIELDS},
            ad_account_id="123",
        )
        with pytest.raises(PermanentError, match="cardinality exceeded"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/act_123/ads",
                body={
                    "name": "Source ad",
                    "adset_id": "601",
                    "creative": {"creative_id": "401"},
                    "status": "PAUSED",
                },
            )

        await client.execute_graph_call(
            method="GET",
            endpoint="/501",
            query_params={"fields": "id,status,daily_budget"},
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="GET",
            endpoint="/601",
            query_params={
                "fields": ("id,campaign_id,status,daily_budget,lifetime_budget,start_time")
            },
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="GET",
            endpoint="/601/ads",
            query_params={"fields": DUPLICATE_VERIFY_AD_FIELDS, "limit": "100"},
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="POST",
            endpoint="/701",
            query_params={},
            body_json={"status": "PAUSED"},
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="GET",
            endpoint="/701",
            query_params={"fields": "id,status,effective_status"},
            ad_account_id="123",
        )

    assert breaker.call.await_count == 15
    grant_calls = [
        call
        for call in engine._test_connection.execute.await_args_list
        if "INSERT INTO browser_operation_capability_uses" in str(call.args[0])
    ]
    assert len(grant_calls) == 15


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "create_response",
    [
        {"data": [{"id": "501"}]},
        {"campaign_id": "501"},
        {"id": "501", "campaign_id": "501"},
    ],
    ids=["nested-id", "fallback-key", "conflicting-extra-key"],
)
async def test_duplicate_campaign_create_rejects_non_exact_result_without_provenance(
    monkeypatch: pytest.MonkeyPatch,
    create_response: dict[str, object],
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine, breaker = _duplicate_client(
        responses=[*_duplicate_source_responses(), create_response]
    )

    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        await _load_duplicate_sources(client)
        with pytest.raises(AmbiguousResultError, match="schema is not exact"):
            await _create_duplicate_campaign(client)
        with pytest.raises(PermanentError, match="not authorized"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/987654321/copies",
                body={
                    "campaign_id": "501",
                    "deep_copy": False,
                    "status_option": "PAUSED",
                },
            )

    assert breaker.call.await_count == 4
    assert _duplicate_grant_count(engine) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "colliding_id",
    ["111", "987654321", "301", "401"],
    ids=["source-campaign", "source-adset", "source-ad", "source-creative"],
)
async def test_duplicate_create_rejects_every_loaded_source_id_collision(
    monkeypatch: pytest.MonkeyPatch,
    colliding_id: str,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine, breaker = _duplicate_client(
        responses=[*_duplicate_source_responses(), {"id": colliding_id}]
    )

    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        await _load_duplicate_sources(client)
        with pytest.raises(AmbiguousResultError, match="collides"):
            await _create_duplicate_campaign(client)

    assert breaker.call.await_count == 4
    assert _duplicate_grant_count(engine) == 4


@pytest.mark.asyncio
async def test_duplicate_candidates_require_typed_proof_before_every_downstream_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine, breaker = _duplicate_client(
        responses=[
            *_duplicate_source_responses(),
            {"id": "501"},
            {
                "id": "501",
                "account_id": "123",
                "name": "Campaign 1",
                "objective": "OUTCOME_SALES",
                "status": "PAUSED",
            },
            {"copied_adset_id": "601"},
            {
                "id": "601",
                "account_id": "123",
                "campaign_id": "501",
                "status": "PAUSED",
            },
            {"success": True},
            {"id": "701"},
            {
                "id": "701",
                "account_id": "123",
                "campaign_id": "501",
                "adset_id": "601",
                "name": "Source ad",
                "status": "PAUSED",
                "creative": {"id": "401"},
            },
        ]
    )

    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        await _load_duplicate_sources(client)
        await _create_duplicate_campaign(client)
        with pytest.raises(PermanentError, match="not authorized"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/987654321/copies",
                body={
                    "campaign_id": "501",
                    "deep_copy": False,
                    "status_option": "PAUSED",
                },
            )
        await client.execute_graph_call(
            method="GET",
            endpoint="/501",
            query_params={"fields": DUPLICATE_PROVE_CAMPAIGN_FIELDS},
            ad_account_id="123",
        )

        await client.execute_graph_call(
            method="POST",
            endpoint="/987654321/copies",
            query_params={},
            body_json={
                "campaign_id": "501",
                "deep_copy": False,
                "status_option": "PAUSED",
            },
            ad_account_id="123",
        )
        with pytest.raises(PermanentError, match="provenance"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/601",
                body={
                    "name": "Ad set 1",
                    "status": "PAUSED",
                    "start_time": "2026-08-01T00:00:00Z",
                    "daily_budget": 5000,
                },
            )
        await client.execute_graph_call(
            method="GET",
            endpoint="/601",
            query_params={"fields": DUPLICATE_PROVE_ADSET_FIELDS},
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="POST",
            endpoint="/601",
            query_params={},
            body_json={
                "name": "Ad set 1",
                "status": "PAUSED",
                "start_time": "2026-08-01T00:00:00Z",
                "daily_budget": 5000,
            },
            ad_account_id="123",
        )

        await client.execute_graph_call(
            method="POST",
            endpoint="/act_123/ads",
            query_params={},
            body_json={
                "name": "Source ad",
                "adset_id": "601",
                "creative": {"creative_id": "401"},
                "status": "PAUSED",
            },
            ad_account_id="123",
        )
        with pytest.raises(PermanentError, match="provenance"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/701",
                body={"status": "PAUSED"},
            )
        await client.execute_graph_call(
            method="GET",
            endpoint="/701",
            query_params={"fields": DUPLICATE_PROVE_AD_FIELDS},
            ad_account_id="123",
        )
        cleanup_authorization = await _prepare_campaign_graph(
            client,
            method="POST",
            endpoint="/701",
            body={"status": "PAUSED"},
        )

    assert cleanup_authorization["authorized_caller"] == "meta_api"
    assert breaker.call.await_count == 10
    assert _duplicate_grant_count(engine) == 11


@pytest.mark.asyncio
async def test_duplicate_nested_copy_response_cannot_promote_or_configure_source_adset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine, breaker = _duplicate_client(
        responses=[
            *_duplicate_source_responses(),
            {"id": "501"},
            {
                "id": "501",
                "account_id": "123",
                "name": "Campaign 1",
                "objective": "OUTCOME_SALES",
                "status": "PAUSED",
            },
            {"data": [{"id": "987654321"}]},
        ]
    )

    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        await _load_duplicate_sources(client)
        await _create_duplicate_campaign(client)
        await client.execute_graph_call(
            method="GET",
            endpoint="/501",
            query_params={"fields": DUPLICATE_PROVE_CAMPAIGN_FIELDS},
            ad_account_id="123",
        )
        with pytest.raises(AmbiguousResultError, match="schema is not exact"):
            await client.execute_graph_call(
                method="POST",
                endpoint="/987654321/copies",
                query_params={},
                body_json={
                    "campaign_id": "501",
                    "deep_copy": False,
                    "status_option": "PAUSED",
                },
                ad_account_id="123",
            )
        with pytest.raises(PermanentError, match="provenance"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/987654321",
                body={
                    "name": "Ad set 1",
                    "status": "PAUSED",
                    "start_time": "2026-08-01T00:00:00Z",
                    "daily_budget": 5000,
                },
            )

    assert breaker.call.await_count == 6
    assert _duplicate_grant_count(engine) == 6


@pytest.mark.asyncio
async def test_duplicate_copy_rejects_cross_type_created_id_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine, breaker = _duplicate_client(
        responses=[
            *_duplicate_source_responses(),
            {"id": "501"},
            {
                "id": "501",
                "account_id": "123",
                "name": "Campaign 1",
                "objective": "OUTCOME_SALES",
                "status": "PAUSED",
            },
            {"copied_adset_id": "501"},
        ]
    )

    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        await _load_duplicate_sources(client)
        await _create_duplicate_campaign(client)
        await client.execute_graph_call(
            method="GET",
            endpoint="/501",
            query_params={"fields": DUPLICATE_PROVE_CAMPAIGN_FIELDS},
            ad_account_id="123",
        )
        with pytest.raises(AmbiguousResultError, match="collides"):
            await client.execute_graph_call(
                method="POST",
                endpoint="/987654321/copies",
                query_params={},
                body_json={
                    "campaign_id": "501",
                    "deep_copy": False,
                    "status_option": "PAUSED",
                },
                ad_account_id="123",
            )

    assert breaker.call.await_count == 6
    assert _duplicate_grant_count(engine) == 6


@pytest.mark.asyncio
async def test_duplicate_same_cabinet_adset_with_wrong_parent_cannot_be_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine, breaker = _duplicate_client(
        responses=[
            *_duplicate_source_responses(),
            {"id": "501"},
            {
                "id": "501",
                "account_id": "123",
                "name": "Campaign 1",
                "objective": "OUTCOME_SALES",
                "status": "PAUSED",
            },
            {"copied_adset_id": "601"},
            {
                "id": "601",
                "account_id": "123",
                "campaign_id": "111",
                "status": "PAUSED",
            },
        ]
    )

    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        await _load_duplicate_sources(client)
        await _create_duplicate_campaign(client)
        await client.execute_graph_call(
            method="GET",
            endpoint="/501",
            query_params={"fields": DUPLICATE_PROVE_CAMPAIGN_FIELDS},
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="POST",
            endpoint="/987654321/copies",
            query_params={},
            body_json={
                "campaign_id": "501",
                "deep_copy": False,
                "status_option": "PAUSED",
            },
            ad_account_id="123",
        )
        with pytest.raises(PermanentError, match="wrong campaign parent"):
            await client.execute_graph_call(
                method="GET",
                endpoint="/601",
                query_params={"fields": DUPLICATE_PROVE_ADSET_FIELDS},
                ad_account_id="123",
            )
        with pytest.raises(PermanentError, match="provenance"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/601",
                body={
                    "name": "Ad set 1",
                    "status": "PAUSED",
                    "start_time": "2026-08-01T00:00:00Z",
                    "daily_budget": 5000,
                },
            )

    assert breaker.call.await_count == 7
    assert _duplicate_grant_count(engine) == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ad_response",
    [
        {"data": [{"id": "701"}]},
        {"ad_id": "701"},
        {"id": "701", "ad_id": "702"},
    ],
    ids=["nested-id", "fallback-key", "conflicting-extra-key"],
)
async def test_duplicate_ad_create_rejects_non_exact_result_without_cleanup_provenance(
    monkeypatch: pytest.MonkeyPatch,
    ad_response: dict[str, object],
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client, engine, breaker = _duplicate_client(
        responses=[
            *_duplicate_source_responses(),
            {"id": "501"},
            {
                "id": "501",
                "account_id": "123",
                "name": "Campaign 1",
                "objective": "OUTCOME_SALES",
                "status": "PAUSED",
            },
            {"copied_adset_id": "601"},
            {
                "id": "601",
                "account_id": "123",
                "campaign_id": "501",
                "status": "PAUSED",
            },
            {"success": True},
            ad_response,
        ]
    )

    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        await _load_duplicate_sources(client)
        await _create_duplicate_campaign(client)
        await client.execute_graph_call(
            method="GET",
            endpoint="/501",
            query_params={"fields": DUPLICATE_PROVE_CAMPAIGN_FIELDS},
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="POST",
            endpoint="/987654321/copies",
            query_params={},
            body_json={
                "campaign_id": "501",
                "deep_copy": False,
                "status_option": "PAUSED",
            },
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="GET",
            endpoint="/601",
            query_params={"fields": DUPLICATE_PROVE_ADSET_FIELDS},
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="POST",
            endpoint="/601",
            query_params={},
            body_json={
                "name": "Ad set 1",
                "status": "PAUSED",
                "start_time": "2026-08-01T00:00:00Z",
                "daily_budget": 5000,
            },
            ad_account_id="123",
        )
        with pytest.raises(AmbiguousResultError, match="schema is not exact"):
            await client.execute_graph_call(
                method="POST",
                endpoint="/act_123/ads",
                query_params={},
                body_json={
                    "name": "Source ad",
                    "adset_id": "601",
                    "creative": {"creative_id": "401"},
                    "status": "PAUSED",
                },
                ad_account_id="123",
            )
        with pytest.raises(PermanentError, match="provenance"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/701",
                body={"status": "PAUSED"},
            )

    assert breaker.call.await_count == 9
    assert _duplicate_grant_count(engine) == 9


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "endpoint", "query", "body"),
    [
        ("POST", "/999999999", {}, {"status": "ACTIVE"}),
        ("GET", "/999999999", {"fields": "id,status"}, None),
        ("GET", "/act_123/campaigns", {"fields": "id"}, None),
        ("DELETE", "/987654321", {}, None),
    ],
)
async def test_meta_api_duplicate_rejects_arbitrary_graph_calls_before_grant(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    endpoint: str,
    query: dict[str, str],
    body: dict[str, object] | None,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client = MetaApiClient(
        session_id="session-exact",
        operation_engine=_operation_engine(_live_row(caller="meta_api")),
    )
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )
    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError):
            await _prepare_campaign_graph(
                client,
                method=method,
                endpoint=endpoint,
                query_params=query,
                body=body,
            )
    assert client._operation_engine._test_connection.execute.await_count == 2


@pytest.mark.asyncio
async def test_meta_api_duplicate_recovery_is_checkpoint_bound_and_pause_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    checkpoint: dict[str, object] = {
        "checkpoint_type": "duplicate_adset_structure",
        "checkpoint_version": 2,
        "recovery_requested": True,
        "created_ids": {
            "campaigns": ["501"],
            "adsets": ["601"],
            "ads": ["701"],
        },
    }
    client, engine, _breaker = _duplicate_client(
        row=_live_row(caller="meta_api", result=checkpoint),
        responses=[
            {
                "id": "601",
                "account_id": "123",
                "campaign_id": "501",
                "status": "ACTIVE",
            },
            {
                "id": "501",
                "account_id": "123",
                "name": "Campaign 1",
                "objective": "OUTCOME_SALES",
                "status": "ACTIVE",
            },
            {
                "id": "701",
                "account_id": "123",
                "campaign_id": "501",
                "adset_id": "601",
                "name": "Recovered ad",
                "status": "ACTIVE",
                "creative": {"id": "401"},
            },
        ],
    )
    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="typed proof"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/601",
                body={"status": "PAUSED"},
            )
        await client.execute_graph_call(
            method="GET",
            endpoint="/601",
            query_params={"fields": DUPLICATE_PROVE_ADSET_FIELDS},
            ad_account_id="123",
        )
        with pytest.raises(PermanentError, match="typed proof"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/601",
                body={"status": "PAUSED"},
            )
        await client.execute_graph_call(
            method="GET",
            endpoint="/501",
            query_params={"fields": DUPLICATE_PROVE_CAMPAIGN_FIELDS},
            ad_account_id="123",
        )
        await client.execute_graph_call(
            method="GET",
            endpoint="/701",
            query_params={"fields": DUPLICATE_PROVE_AD_FIELDS},
            ad_account_id="123",
        )
        pause_authorization = await _prepare_campaign_graph(
            client,
            method="POST",
            endpoint="/601",
            body={"status": "PAUSED"},
        )
        status_authorization = await _prepare_campaign_graph(
            client,
            method="GET",
            endpoint="/601",
            query_params={"fields": "id,status,effective_status"},
        )
        with pytest.raises(PermanentError, match="typed proof"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/601",
                body={"status": "ACTIVE"},
            )
        with pytest.raises(PermanentError, match="persisted checkpoint"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/999",
                body={"status": "PAUSED"},
            )
        with pytest.raises(PermanentError, match="persisted checkpoint"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/act_123/campaigns",
                body={
                    "name": "Campaign 1",
                    "objective": "OUTCOME_SALES",
                    "special_ad_categories": ["NONE"],
                    "status": "PAUSED",
                },
            )

    assert pause_authorization["authorized_caller"] == "meta_api"
    assert status_authorization["authorized_caller"] == "meta_api"
    grant_calls = [
        call
        for call in engine._test_connection.execute.await_args_list
        if "INSERT INTO browser_operation_capability_uses" in str(call.args[0])
    ]
    assert len(grant_calls) == 5


@pytest.mark.asyncio
async def test_duplicate_recovery_wrong_parent_proof_never_unlocks_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    checkpoint: dict[str, object] = {
        "checkpoint_type": "duplicate_adset_structure",
        "checkpoint_version": 2,
        "recovery_requested": True,
        "created_ids": {
            "campaigns": ["501"],
            "adsets": ["601"],
            "ads": [],
        },
    }
    client, engine, breaker = _duplicate_client(
        row=_live_row(caller="meta_api", result=checkpoint),
        responses=[
            {
                "id": "601",
                "account_id": "123",
                "campaign_id": "111",
                "status": "ACTIVE",
            }
        ],
    )

    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="does not match its parent"):
            await client.execute_graph_call(
                method="GET",
                endpoint="/601",
                query_params={"fields": DUPLICATE_PROVE_ADSET_FIELDS},
                ad_account_id="123",
            )
        with pytest.raises(PermanentError, match="typed proof"):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/601",
                body={"status": "PAUSED"},
            )

    assert breaker.call.await_count == 1
    assert _duplicate_grant_count(engine) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "checkpoint",
    [
        {
            "checkpoint_type": "duplicate_adset_structure",
            "checkpoint_version": 2,
            "recovery_requested": 1,
            "created_ids": {"campaigns": ["501"], "adsets": [], "ads": []},
        },
        {
            "checkpoint_type": "duplicate_adset_structure",
            "checkpoint_version": 2,
            "recovery_requested": True,
            "created_ids": {"campaigns": [501], "adsets": [], "ads": []},
        },
        {
            "checkpoint_type": "duplicate_adset_structure",
            "checkpoint_version": 2,
            "recovery_requested": True,
            "created_ids": {
                "campaigns": ["501"],
                "adsets": [],
                "ads": [],
                "creatives": [],
            },
        },
        {
            "checkpoint_type": "other_workflow",
            "checkpoint_version": 2,
            "recovery_requested": True,
            "created_ids": {"campaigns": ["501"], "adsets": [], "ads": []},
        },
        {
            "recovery_requested": True,
            "created_ids": {"campaigns": ["501"], "adsets": [], "ads": []},
        },
        {
            "checkpoint_type": "duplicate_adset_structure",
            "checkpoint_version": 1,
            "recovery_requested": True,
            "created_ids": {"campaigns": ["501"], "adsets": [], "ads": []},
        },
        {
            "checkpoint_type": "duplicate_adset_structure",
            "checkpoint_version": 2,
            "recovery_requested": True,
            "created_ids": {
                "campaigns": ["501"],
                "adsets": ["501"],
                "ads": [],
            },
        },
        {
            "checkpoint_type": "duplicate_adset_structure",
            "checkpoint_version": 2,
            "recovery_requested": True,
            "created_ids": {
                "campaigns": ["111"],
                "adsets": [],
                "ads": [],
            },
        },
        {
            "checkpoint_type": "duplicate_adset_structure",
            "checkpoint_version": 2,
            "recovery_requested": True,
            "created_ids": {
                "campaigns": [],
                "adsets": ["601"],
                "ads": [],
            },
        },
    ],
)
async def test_meta_api_duplicate_rejects_malformed_recovery_checkpoint_before_grant(
    monkeypatch: pytest.MonkeyPatch,
    checkpoint: dict[str, object],
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    engine = _operation_engine(_live_row(caller="meta_api", result=checkpoint))
    client = MetaApiClient(
        session_id="session-exact",
        operation_engine=engine,
    )
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )
    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError):
            await _prepare_campaign_graph(
                client,
                method="POST",
                endpoint="/501",
                body={"status": "PAUSED"},
            )
    assert engine._test_connection.execute.await_count == 2


@pytest.mark.asyncio
async def test_python_client_rejects_signed_status_semantic_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    client = MetaApiClient(
        session_id="session-exact",
        operation_engine=_operation_engine(_live_row()),
    )
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )
    activate_operation = graph_operation_binding(
        method="POST",
        endpoint="/987654321",
        query_params={"status": "ACTIVE"},
        body_json="",
    )

    with client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="claimed ad mutation"):
            await client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=activate_operation,
                ad_account_id="123",
                graph_method="POST",
                graph_endpoint="/987654321",
                graph_query_params={"status": "ACTIVE"},
                graph_body_json="",
            )

    traversal_client = MetaApiClient(
        session_id="session-exact",
        operation_engine=_operation_engine(_live_row()),
    )
    traversal_client._stub = client._stub
    with traversal_client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="target is invalid"):
            await traversal_client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=graph_operation_binding(
                    method="POST",
                    endpoint="/987654321/../999",
                    query_params={"status": "PAUSED"},
                    body_json="",
                ),
                ad_account_id="123",
                graph_method="POST",
                graph_endpoint="/987654321/../999",
                graph_query_params={"status": "PAUSED"},
                graph_body_json="",
            )


@pytest.mark.parametrize(
    ("mutation_kind", "desired_status"),
    (
        ("pause_ad", "PAUSED"),
        ("activate_ad", "ACTIVE"),
    ),
)
@pytest.mark.asyncio
async def test_owner_worker_authorizes_exact_single_ad_status_action(
    monkeypatch: pytest.MonkeyPatch,
    mutation_kind: str,
    desired_status: str,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    row = _live_row(caller="meta_api")
    row["lane"] = "interactive"
    row["payload"] = {
        "mutation_kind": mutation_kind,
        "target_id": "987654321",
        "ad_account_id": "123",
    }
    operation = graph_operation_binding(
        method="POST",
        endpoint="/987654321",
        query_params={"status": desired_status},
        body_json="",
    )
    client = MetaApiClient(
        session_id="session-exact",
        operation_engine=_operation_engine(row),
    )
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )

    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        authorization = await client.prepare_operation_authorization(
            rpc="execute_graph_call",
            operation=operation,
            ad_account_id="123",
            graph_method="POST",
            graph_endpoint="/987654321",
            graph_query_params={"status": desired_status},
            graph_body_json="",
        )

    assert authorization["task_id"] == 1842

    automatic_row = {**row, "requested_by": "bot_auto_stop"}
    automatic_engine = _operation_engine(automatic_row)
    automatic_client = MetaApiClient(
        session_id="session-exact",
        operation_engine=automatic_engine,
    )
    automatic_client._stub = client._stub
    with automatic_client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="caller/requester binding"):
            await automatic_client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=operation,
                ad_account_id="123",
                graph_method="POST",
                graph_endpoint="/987654321",
                graph_query_params={"status": desired_status},
                graph_body_json="",
            )

    assert automatic_engine._test_connection.execute.await_count == 2


@pytest.mark.asyncio
async def test_autopause_caller_rejects_manual_requester_even_for_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    row = _live_row()
    row["requested_by"] = "operator:web"
    engine = _operation_engine(row)
    client = MetaApiClient(session_id="session-exact", operation_engine=engine)
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )

    with client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="caller/requester binding"):
            await client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=_STATUS_OPERATION,
                ad_account_id="123",
                **_STATUS_GRAPH_SEMANTICS,
            )

    assert engine._test_connection.execute.await_count == 2


@pytest.mark.parametrize("mutation_kind", ("activate_ad", "bulk_status_change"))
@pytest.mark.asyncio
async def test_autopause_caller_cannot_authorize_non_pause_mutation(
    monkeypatch: pytest.MonkeyPatch,
    mutation_kind: str,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    row = _live_row()
    if mutation_kind == "activate_ad":
        row["payload"] = {
            "mutation_kind": mutation_kind,
            "target_id": "987654321",
            "ad_account_id": "123",
        }
        endpoint = "/987654321"
        query_params = {"status": "ACTIVE"}
    else:
        row["payload"] = {
            "mutation_kind": mutation_kind,
            "target_id": "bulk:1",
            "ad_account_id": "123",
            "params": {"action": "activate", "ad_ids": ["987654321"]},
        }
        endpoint = "/"
        query_params = {
            "batch": json.dumps([{"method": "POST", "relative_url": "987654321?status=ACTIVE"}])
        }
    operation = graph_operation_binding(
        method="POST",
        endpoint=endpoint,
        query_params=query_params,
        body_json="",
    )
    engine = _operation_engine(row)
    client = MetaApiClient(session_id="session-exact", operation_engine=engine)
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )

    with client.operation_authority(
        caller="autopause",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="caller/mutation binding"):
            await client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=operation,
                ad_account_id="123",
                graph_method="POST",
                graph_endpoint=endpoint,
                graph_query_params=query_params,
                graph_body_json="",
            )

    assert engine._test_connection.execute.await_count == 2


@pytest.mark.parametrize(
    ("action", "desired_status"),
    (("pause", "PAUSED"), ("activate", "ACTIVE")),
)
@pytest.mark.asyncio
async def test_owner_worker_binds_bulk_task_to_exact_targets_and_action(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    desired_status: str,
) -> None:
    monkeypatch.setenv("BROWSER_OPERATION_CAPABILITY_SECRET", _SECRET)
    row = _live_row(caller="meta_api")
    row["payload"] = {
        "mutation_kind": "bulk_status_change",
        "target_id": "bulk:2",
        "ad_account_id": "123",
        "params": {
            "ad_ids": ["111", "222"],
            "action": action,
        },
    }
    exact_batch = json.dumps(
        [
            {"method": "POST", "relative_url": f"111?status={desired_status}"},
            {"method": "POST", "relative_url": f"222?status={desired_status}"},
        ]
    )
    exact_operation = graph_operation_binding(
        method="POST",
        endpoint="/",
        query_params={"batch": exact_batch},
        body_json="",
    )
    client = MetaApiClient(
        session_id="session-exact",
        operation_engine=_operation_engine(row),
    )
    client._stub = SimpleNamespace(
        CheckMetaApiHealth=AsyncMock(
            return_value=SimpleNamespace(
                healthy=True,
                browser_contract_version=BROWSER_CONTRACT_VERSION,
                session_id="session-exact",
                vision_profile_id="profile-exact",
            )
        )
    )
    with client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        authorization = await client.prepare_operation_authorization(
            rpc="execute_graph_call",
            operation=exact_operation,
            ad_account_id="123",
            graph_method="POST",
            graph_endpoint="/",
            graph_query_params={"batch": exact_batch},
            graph_body_json="",
        )
    assert authorization["task_id"] == 1842

    tampered_batch = json.dumps(
        [
            {"method": "POST", "relative_url": f"111?status={desired_status}"},
            {
                "method": "POST",
                "relative_url": (
                    "999?status=ACTIVE" if desired_status == "PAUSED" else "999?status=PAUSED"
                ),
            },
        ]
    )
    tampered_client = MetaApiClient(
        session_id="session-exact",
        operation_engine=_operation_engine(row),
    )
    tampered_client._stub = client._stub
    with tampered_client.operation_authority(
        caller="meta_api",
        task_id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
        vision_profile_id="profile-exact",
    ):
        with pytest.raises(PermanentError, match="claimed bulk mutation"):
            await tampered_client.prepare_operation_authorization(
                rpc="execute_graph_call",
                operation=graph_operation_binding(
                    method="POST",
                    endpoint="/",
                    query_params={"batch": tampered_batch},
                    body_json="",
                ),
                ad_account_id="123",
                graph_method="POST",
                graph_endpoint="/",
                graph_query_params={"batch": tampered_batch},
                graph_body_json="",
            )
