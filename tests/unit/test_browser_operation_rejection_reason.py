# -*- coding: utf-8 -*-
"""Отказ собственной авторизации браузера называет причину и остаётся REJECTED.

Инвариант: каждый предикат browser-agent, дающий gRPC PERMISSION_DENIED,
срабатывает строго ДО первого fetch в Meta. Значит исход — доказанный отказ
до отправки (REJECTED), а не путь «часть изменений принята», и причина
различима машинно, а не схлопнута в одну строку.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import grpc
import pytest

from core.meta_api.client import (
    BROWSER_OPERATION_REJECTION_METADATA_KEY,
    BROWSER_OPERATION_REJECTION_REASONS,
    MetaApiClient,
)
from core.meta_api.errors import (
    AmbiguousResultError,
    BrowserOperationRejectedError,
    PermanentError,
    PreDispatchRejectedError,
)
from core.meta_api.upload import MediaUploader

_SERVICE_TS = (
    Path(__file__).resolve().parents[2]
    / "services"
    / "browser-agent"
    / "src"
    / "meta-api"
    / "service.ts"
)


def _rpc(
    status: grpc.StatusCode,
    *,
    reason: str | None = None,
    details: str = "browser-agent rejected the operation",
) -> SimpleNamespace:
    trailers: tuple[tuple[str, str], ...] = (
        (("x-request-id", "irrelevant"),)
        if reason is None
        else (
            ("x-request-id", "irrelevant"),
            (BROWSER_OPERATION_REJECTION_METADATA_KEY, reason),
        )
    )
    return SimpleNamespace(
        code=lambda: status,
        details=lambda: details,
        trailing_metadata=lambda: trailers,
    )


@pytest.mark.parametrize("reason", sorted(BROWSER_OPERATION_REJECTION_REASONS))
def test_reason_code_reaches_python_as_a_proven_pre_send_rejection(reason: str) -> None:
    mapped = MetaApiClient._grpc_to_meta_error(
        _rpc(grpc.StatusCode.PERMISSION_DENIED, reason=reason),
        endpoint="/act_1/ads",
    )

    assert isinstance(mapped, BrowserOperationRejectedError)
    assert isinstance(mapped, PreDispatchRejectedError)
    assert not isinstance(mapped, AmbiguousResultError)
    assert mapped.reason_code == reason
    assert BROWSER_OPERATION_REJECTION_REASONS[reason] in str(mapped)


def test_reason_codes_are_distinguishable_and_not_one_collapsed_string() -> None:
    messages = {
        str(
            MetaApiClient._grpc_to_meta_error(
                _rpc(grpc.StatusCode.PERMISSION_DENIED, reason=reason),
                endpoint="/act_1/ads",
            )
        )
        for reason in BROWSER_OPERATION_REJECTION_REASONS
    }

    assert len(messages) == len(BROWSER_OPERATION_REJECTION_REASONS)


def test_rejection_message_carries_no_raw_browser_details() -> None:
    mapped = MetaApiClient._grpc_to_meta_error(
        _rpc(
            grpc.StatusCode.PERMISSION_DENIED,
            reason="capability_invalid",
            details="EAAG-secret-token leaked into details",
        ),
        endpoint="/act_1/ads",
    )

    assert "EAAG" not in str(mapped)


def test_upload_rejection_is_the_same_proven_pre_send_rejection() -> None:
    mapped = MediaUploader._grpc_to_error(
        _rpc(grpc.StatusCode.PERMISSION_DENIED, reason="caller_not_authorized"),
        endpoint="/act_1/adimages",
    )

    assert isinstance(mapped, BrowserOperationRejectedError)
    assert isinstance(mapped, PreDispatchRejectedError)
    assert mapped.reason_code == "caller_not_authorized"


def test_permission_denied_without_a_reason_code_is_not_claimed_proven() -> None:
    # Трейлера нет — значит доказательства pre-send отказа нет. Старое,
    # более осторожное поведение сохраняется: не выдаём догадку за факт.
    mapped = MetaApiClient._grpc_to_meta_error(
        _rpc(grpc.StatusCode.PERMISSION_DENIED),
        endpoint="/act_1/ads",
    )

    assert isinstance(mapped, PermanentError)
    assert not isinstance(mapped, PreDispatchRejectedError)


def test_unknown_reason_code_is_not_promoted_to_a_proven_rejection() -> None:
    mapped = MetaApiClient._grpc_to_meta_error(
        _rpc(grpc.StatusCode.PERMISSION_DENIED, reason="reason_from_a_newer_agent"),
        endpoint="/act_1/ads",
    )

    assert isinstance(mapped, PermanentError)
    assert not isinstance(mapped, PreDispatchRejectedError)


def test_aborted_capability_consume_stays_reconcilable() -> None:
    # Списанный грант мог пересечь границу: это ручная сверка, а не REJECTED.
    mapped = MetaApiClient._grpc_to_meta_error(
        _rpc(grpc.StatusCode.ABORTED, reason="capability_invalid"),
        endpoint="/act_1/ads",
    )

    assert isinstance(mapped, AmbiguousResultError)


def test_python_reason_codes_mirror_the_browser_agent_predicate_table() -> None:
    # Код причины — контракт между двумя языками. Разошедшийся словарь
    # молча вернёт отказ в путь «часть изменений принята».
    source = _SERVICE_TS.read_text(encoding="utf-8")
    table = re.search(
        r"OPERATION_REJECTION_PREDICATES[^\[]*\[(.*?)\n\];",
        source,
        re.DOTALL,
    )
    assert table is not None, "predicate table not found in service.ts"
    ts_reasons = set(re.findall(r"'[^']+',\s*'([a-z_]+)'", table.group(1)))

    assert ts_reasons == set(BROWSER_OPERATION_REJECTION_REASONS)


def test_metadata_key_matches_the_browser_agent_trailer() -> None:
    source = _SERVICE_TS.read_text(encoding="utf-8")
    match = re.search(
        r"BROWSER_OPERATION_REJECTION_METADATA_KEY\s*=\s*'([^']+)'",
        source,
    )

    assert match is not None
    assert match.group(1) == BROWSER_OPERATION_REJECTION_METADATA_KEY
