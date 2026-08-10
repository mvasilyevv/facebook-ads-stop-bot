"""Architecture contract for the single fail-closed local lifecycle."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _make_target_body(source: str, target: str) -> str:
    match = re.search(
        rf"^{re.escape(target)}:[^\n]*\n(?P<body>(?:\t[^\n]*\n)+)",
        source,
        re.MULTILINE,
    )
    assert match is not None, f"missing Make target {target}"
    return match.group("body")


def test_make_runtime_targets_delegate_to_the_local_launcher() -> None:
    source = (ROOT / "Makefile").read_text(encoding="utf-8")

    expected_arguments = {
        "start": "",
        "docker-up": "",
        "stop": " --down",
        "docker-down": " --down",
        "logs": " --logs",
    }
    for target, arguments in expected_arguments.items():
        body = _make_target_body(source, target)
        assert f"FB_AGENT_PROFILE=local ./scripts/run-local.sh{arguments}" in body

    for retired_host_target in (
        "observer",
        "meta-api-worker",
        "cabinet-scheduler",
        "telegram-delivery-worker",
        "telegram-update-worker",
        "browser-agent",
        "browser-agent-dev",
        "api",
    ):
        assert (
            re.search(
                rf"^{re.escape(retired_host_target)}\s*:",
                source,
                re.MULTILINE,
            )
            is None
        )


def test_local_launcher_requires_exact_profile_and_explicit_services() -> None:
    source = (ROOT / "scripts/run-local.sh").read_text(encoding="utf-8")

    assert "grep -qx 'FB_AGENT_PROFILE=local'" in source
    assert "export FB_AGENT_PROFILE=local" in source
    assert "LOCAL_SERVICES=(api telegram_delivery_worker telegram_update_worker)" in source
    assert '"${COMPOSE[@]}" up -d postgres' in source
    assert 'if ! "${COMPOSE[@]}" up -d redis; then' in source
    assert '"${COMPOSE[@]}" run --rm migrate' in source
    assert '"${COMPOSE[@]}" up -d "${LOCAL_SERVICES[@]}"' in source
    assert '"${COMPOSE[@]}" up -d' not in source.replace(
        '"${COMPOSE[@]}" up -d postgres',
        "",
    ).replace(
        '"${COMPOSE[@]}" up -d redis',
        "",
    ).replace(
        '"${COMPOSE[@]}" up -d "${LOCAL_SERVICES[@]}"',
        "",
    )


def test_disposable_reset_keeps_all_three_explicit_guards() -> None:
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    body = _make_target_body(source, "reset-disposable-db")

    assert "FB_AGENT_DISPOSABLE_DATABASE_URL" in body
    assert "FB_AGENT_ALLOW_DESTRUCTIVE_RESET" in body
    assert "CONFIRM_DATABASE" in body
    assert 'scripts/apply_schema.py --confirm-drop --confirm-database "$(CONFIRM_DATABASE)"' in body


def test_db_wait_uses_container_database_identity() -> None:
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    body = _make_target_body(source, "db-wait")

    assert 'pg_isready -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' in body
    assert "fb_stop_bot -d fb_stop_bot" not in body
