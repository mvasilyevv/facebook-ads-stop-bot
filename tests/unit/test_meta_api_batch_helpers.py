"""Canonical Graph status-batch helper tests."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from core.meta_api.errors import AmbiguousResultError
from core.meta_api.mutations._batch_helpers import (
    MAX_BATCH_ENTRIES,
    build_batch_payload,
    make_batch_entry,
    parse_batch_response,
    validate_relative_url,
)
from core.meta_api.mutations.bulk_status_change import BulkStatusChangeHandler
from core.meta_api.schemas import MetaMutationPayload


def test_make_batch_entry_builds_independent_status_write() -> None:
    assert make_batch_entry(method="POST", relative_url="123?status=PAUSED") == {
        "method": "POST",
        "relative_url": "123?status=PAUSED",
    }


@pytest.mark.parametrize(
    ("method", "url", "message"),
    [
        ("", "me", "method"),
        ("PATCH", "me", "method"),
        ("POST", "", "relative_url"),
        ("POST", "/123", "начинаться"),
        ("POST", "https://graph.facebook.com/123", "относительным"),
        ("POST", "{result=x:$.id}", "templates"),
    ],
)
def test_make_batch_entry_rejects_noncanonical_input(
    method: str,
    url: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        make_batch_entry(method=method, relative_url=url)


def test_validate_relative_url_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="str"):
        validate_relative_url(123)  # type: ignore[arg-type]


def test_build_batch_payload_rejects_empty_or_oversized() -> None:
    with pytest.raises(ValueError, match="пустой"):
        build_batch_payload([])
    entries = [
        make_batch_entry(method="POST", relative_url=f"{index}?status=PAUSED")
        for index in range(MAX_BATCH_ENTRIES + 1)
    ]
    with pytest.raises(ValueError, match="слишком много"):
        build_batch_payload(entries)


def test_build_batch_payload_serializes_entries() -> None:
    entries = [
        make_batch_entry(method="POST", relative_url="1?status=PAUSED"),
        make_batch_entry(method="POST", relative_url="2?status=PAUSED"),
    ]
    assert json.loads(build_batch_payload(entries)) == entries


def test_parse_batch_response_normalizes_success_and_failure() -> None:
    raw = [
        {"code": 200, "body": '{"success":true}'},
        {
            "code": 400,
            "body": '{"error":{"message":"bad request","code":100}}',
        },
    ]
    rows = parse_batch_response(raw)
    assert rows[0]["success"] is True
    assert rows[0]["body"] == {"success": True}
    assert rows[1]["success"] is False
    assert rows[1]["error"] == "bad request"


def test_parse_batch_response_accepts_data_envelope_and_null_item() -> None:
    rows = parse_batch_response(
        {"data": [{"code": 200, "body": "{}"}, None]},
        expected_count=2,
    )
    assert rows[0]["success"] is True
    assert rows[1]["success"] is False
    assert rows[1]["error"] == "null_response"


@pytest.mark.parametrize(
    ("body", "evidence", "error"),
    [
        ('{"success":true}', "confirmed", None),
        ('{"success":false}', "rejected", "mutation_rejected"),
        ("{}", "unknown", "ambiguous_mutation_ack"),
    ],
)
def test_parse_batch_mutation_uses_exact_business_ack(
    body: str,
    evidence: str,
    error: str | None,
) -> None:
    [row] = parse_batch_response(
        [{"code": 200, "body": body}],
        expected_count=1,
        success_evidence="mutation_ack",
    )
    assert row["success"] is (evidence == "confirmed")
    assert row["mutation_evidence"] == evidence
    assert row.get("error") == error


@pytest.mark.asyncio
async def test_bulk_status_change_never_confirms_transport_only_2xx() -> None:
    client = AsyncMock()
    client.execute_graph_call.return_value = [{"code": 200, "body": "{}"}]
    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk:1",
        params={"ad_ids": ["100"], "action": "pause"},
        ad_account_id="act_1",
    )

    with pytest.raises(AmbiguousResultError, match="no exact success=true"):
        await BulkStatusChangeHandler().execute(client, payload)


@pytest.mark.asyncio
async def test_bulk_status_change_preserves_explicit_false_as_rejected() -> None:
    client = AsyncMock()
    client.execute_graph_call.return_value = [{"code": 200, "body": '{"success":false}'}]
    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk:1",
        params={"ad_ids": ["100"], "action": "activate"},
        ad_account_id="act_1",
    )

    result = await BulkStatusChangeHandler().execute(client, payload)

    assert result["success"] is True
    assert result["modified_ids"] == []
    assert result["succeeded"] == 0
    assert result["failed"] == 1
    assert result["sub_results"] == [
        {
            "id": "100",
            "success": False,
            "code": 200,
            "error": "mutation_rejected",
        }
    ]


def test_parse_batch_response_pads_or_rejects_unexpected_shape() -> None:
    padded = parse_batch_response([{"code": 200, "body": "{}"}], expected_count=2)
    assert len(padded) == 2
    assert padded[1]["error"] == "missing_response"

    unexpected = parse_batch_response("not-a-list", expected_count=2)
    assert len(unexpected) == 2
    assert all(row["success"] is False for row in unexpected)
    assert all("unexpected" in row["error"] for row in unexpected)

    oversized = parse_batch_response(
        [{"code": 200, "body": "{}"}, {"code": 200, "body": "{}"}],
        expected_count=1,
    )
    assert len(oversized) == 1
    assert oversized[0]["success"] is False
    assert oversized[0]["error"] == "unexpected_batch_response_count"
