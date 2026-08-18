# -*- coding: utf-8 -*-
"""Локальный прогон не отстаёт от CI.

18.08.2026 правка одной строки интерфейса сломала Playwright. Локально был
прогнан `pnpm -r test`, который Playwright не включает, — поломка доехала до CI
и стоила цикла. Гард требует, чтобы каждая проверка из verify.yml имела
соответствие в scripts/preflight.sh.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = ROOT / "scripts/preflight.sh"

# Команды, которые CI гоняет в verify.yml. Совпадение проверяется по подстроке:
# скрипт волен обернуть команду, но не волен её потерять.
_CI_COMMANDS = (
    "scripts/generate_grpc_stubs.py",
    "ruff check .",
    "scripts.run-migrations-locked",
    "pytest tests/",
    "pnpm run sync:api",
    "pnpm lint",
    "pnpm typecheck",
    "pnpm test",
    "Dockerfile.frontend",
    "Dockerfile.mini-app",
    "npm run lint --prefix services/browser-agent",
    "npm test --prefix services/browser-agent",
    "build-storybook",
    "test:storybook",
    "test:e2e",
    "actionlint",
    "validate_executable_modes.py",
    "validate-platform-configs.sh",
    "shellcheck",
)


def test_preflight_script_exists_and_is_executable() -> None:
    assert _PREFLIGHT.exists(), "нет scripts/preflight.sh — локально нечем прогнать то же, что в CI"
    assert _PREFLIGHT.stat().st_mode & 0o111, "scripts/preflight.sh не исполняемый"


def test_preflight_covers_every_ci_command() -> None:
    source = _PREFLIGHT.read_text(encoding="utf-8")
    missing = [command for command in _CI_COMMANDS if command not in source]
    assert missing == [], (
        "локальный прогон не покрывает проверки CI: "
        + ", ".join(missing)
        + " — их поломку увидит только CI, через двадцать минут"
    )
