"""Отказ импорта обязан называть причину, а не одну безымянную строку.

Прогон 23.08 встал на шаге `adoption`, и наружу ушло ровно
`first-release adoption failed`. Причина осталась внутри контейнера, который к
моменту разбора уже не существовал, — повторить отказ было негде. Поэтому
инвариант: строка отказа несёт тип исключения, а для собственных
операторских ошибок adoption — ещё и сообщение. Чужие исключения печатаются
только типом: их текст может нести DSN.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from core.adoption.bundle import AdoptionValidationError
from core.adoption.repository import AdoptionSemanticMismatchError
from core.adoption.service import (
    AdoptionReceiptConflictError,
    AdoptionTransactionError,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/adopt-first-release.py"
SPEC = importlib.util.spec_from_file_location("adopt_first_release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _fail_with(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    async def boom() -> None:
        raise error

    monkeypatch.setattr(MODULE, "_run", boom)


@pytest.mark.parametrize(
    "error",
    [
        AdoptionValidationError("bundle must contain exactly one owner"),
        AdoptionTransactionError("source database identity is incomplete"),
        AdoptionReceiptConflictError("database adoption receipt is malformed"),
        AdoptionSemanticMismatchError("target semantic verification failed"),
    ],
)
def test_adoption_errors_name_type_and_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    _fail_with(monkeypatch, error)

    assert MODULE.main() == 1

    line = capsys.readouterr().err.strip()
    assert line.startswith("first-release adoption failed: ")
    assert type(error).__name__ in line
    assert str(error) in line


def test_foreign_error_names_only_its_type(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Текст чужого исключения может нести DSN, поэтому наружу идёт только тип."""

    secret = "postgresql://fb_agent:s3cr3t@10.0.0.5:5432/fb_agent"
    _fail_with(monkeypatch, ConnectionRefusedError(f"could not connect to {secret}"))

    assert MODULE.main() == 1

    line = capsys.readouterr().err.strip()
    assert line == "first-release adoption failed: ConnectionRefusedError"
    assert "s3cr3t" not in line
    assert secret not in line


def test_success_stays_silent_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def ok() -> None:
        print("first-release adoption imported; source_fingerprint=abc")

    monkeypatch.setattr(MODULE, "_run", ok)

    assert MODULE.main() == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "imported" in captured.out
