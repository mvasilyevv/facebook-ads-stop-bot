from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP_COMPOSE = ROOT / "deploy/compose/docker-compose.app.yml"
JOBS_COMPOSE = ROOT / "deploy/compose/docker-compose.jobs.yml"

ACTIVE_BUSINESS_PACKAGES = (
    "apps/observer_worker",
    "apps/autopause_worker",
    "apps/meta_api_worker",
    "apps/cleanup_worker",
    "apps/reconciler_worker",
    "apps/health_watchdog",
    "apps/digest_scheduler",
    "apps/tracker_reconciliation_worker",
    "apps/campaign_creator_worker",
)

FORBIDDEN_BUSINESS_IMPORTS = {
    "core.ai_assistant.diagnostics",
    "core.telegram.client",
    "core.telegram.alert_dispatcher",
    "core.alerts.queue",
    "core.alerts.send",
    "core.alerts.drain_worker",
}


def _python_files(relative: str) -> list[Path]:
    return sorted((ROOT / relative).rglob("*.py"))


def _imports(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_single_slot_runtime_uses_only_webhook_telegram_workers() -> None:
    document = yaml.safe_load(APP_COMPOSE.read_text(encoding="utf-8"))
    services = document["services"]
    telegram_services = {name for name in services if "telegram" in name}

    assert telegram_services == {
        "telegram_delivery_worker",
        "telegram_update_worker",
    }
    assert services["telegram_delivery_worker"]["entrypoint"] == [
        "python",
        "-m",
        "apps.telegram_delivery_worker.main",
    ]
    assert services["telegram_update_worker"]["entrypoint"] == [
        "python",
        "-m",
        "apps.telegram_update_worker.main",
    ]
    jobs = yaml.safe_load(JOBS_COMPOSE.read_text(encoding="utf-8"))["services"]
    assert jobs["telegram_webhook_configurator"]["restart"] == "no"
    compose_source = APP_COMPOSE.read_text(encoding="utf-8")
    jobs_source = JOBS_COMPOSE.read_text(encoding="utf-8")
    deploy_source = (ROOT / "fbctl/controller.py").read_text(encoding="utf-8")
    target_sources = "\n".join((compose_source, jobs_source, deploy_source))

    assert "telegram_poller" not in target_sources
    assert "run_telegram_poller.py" not in target_sources
    assert "telegram_delivery_worker" in deploy_source
    assert "telegram_update_worker" in deploy_source
    assert '"app"' in deploy_source
    assert "server-platform-release.sh" not in deploy_source


def test_all_supported_launchers_disable_legacy_long_polling() -> None:
    """No secondary launcher may resurrect getUpdates beside the webhook."""
    entrypoint_source = (ROOT / "docker/worker-entrypoint.sh").read_text(encoding="utf-8")
    compose_source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    local_source = (ROOT / "scripts/run-local.sh").read_text(encoding="utf-8")
    make_source = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "telegram_poller)" not in entrypoint_source
    assert "exec python run_telegram_poller.py" not in entrypoint_source
    assert "\n  telegram_poller:" not in compose_source
    assert "WORKER_TYPE: telegram_poller" not in compose_source
    assert "telegram_poller" not in local_source
    assert not (ROOT / "apps/telegram_poller").exists()
    assert not (ROOT / "run_telegram_poller.py").exists()
    assert not (ROOT / "helm").exists()
    assert not (ROOT / "k8s").exists()

    for retired_target in (
        "telegram",
        "disable-worker",
        "enable-worker",
        "deploy-dry-run",
        "deploy-server",
        "server-preflight",
        "k3s-import",
        "helm-install",
        "helm-uninstall",
        "k8s-logs",
    ):
        assert re.search(rf"^{re.escape(retired_target)}\s*:", make_source, re.MULTILINE) is None
    assert "run_telegram_poller.py" not in make_source
    assert "run_disable_worker.py" not in make_source
    assert "run_enable_worker.py" not in make_source
    assert "./scripts/run-local.sh" in make_source

    assert "apps.telegram_delivery_worker.main" in entrypoint_source
    assert "apps.telegram_update_worker.main" in entrypoint_source
    assert "telegram_delivery_worker:" in compose_source
    assert "telegram_update_worker:" in compose_source


def test_active_business_workers_cannot_send_telegram_directly() -> None:
    offenders: list[str] = []
    files = [path for package in ACTIVE_BUSINESS_PACKAGES for path in _python_files(package)]
    files.append(ROOT / "core/telegram/worker_notify.py")
    for path in files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        forbidden_imports = _imports(tree).intersection(FORBIDDEN_BUSINESS_IMPORTS)
        direct_calls = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "send_message"
                or isinstance(node.func, ast.Name)
                and node.func.id == "send_telegram_via_queue"
            )
        ]
        if forbidden_imports or direct_calls:
            offenders.append(
                f"{path.relative_to(ROOT)} imports={sorted(forbidden_imports)} "
                f"direct_send_lines={direct_calls}"
            )

    assert not offenders, "Business workers bypass durable outbox:\n" + "\n".join(offenders)


def test_active_business_workers_use_typed_notification_facts() -> None:
    """No positional Redis/client argument, markup parsing, or inferred severity."""
    offenders: list[str] = []
    files = [path for package in ACTIVE_BUSINESS_PACKAGES for path in _python_files(package)]
    files.extend(_python_files("core/meta_api"))

    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"notify_owners", "notify_recipients"}
            ):
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            if (
                len(node.args) != 1
                or {
                    "category",
                    "text",
                    "reply_markup",
                    "dedup_key",
                    "dedup_ttl_seconds",
                }.intersection(keywords)
                or not {"event_type", "severity", "title"}.issubset(keywords)
            ):
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} "
                    f"args={len(node.args)} keywords={sorted(keywords)}"
                )

    assert not offenders, "Legacy notification call shape:\n" + "\n".join(offenders)


def test_api_and_menu_configuration_use_the_sanitized_gateway() -> None:
    settings_source = (ROOT / "apps/api/routers/v1/settings_telegram.py").read_text(
        encoding="utf-8"
    )
    menu_source = (ROOT / "core/telegram/menu_button.py").read_text(encoding="utf-8")
    assert "core.telegram.client" not in settings_source
    assert "core.telegram.client" not in menu_source
    assert "TelegramHTMLGateway" in settings_source


def test_telegram_settings_have_no_redis_identity_cache() -> None:
    source = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "apps/api/routers/v1/settings_telegram.py",
            "core/telegram/settings_compute.py",
        )
    )
    assert "DepRedis" not in source
    assert "tg:bot_username" not in source
    assert "redis." not in source.lower()


def test_new_telegram_runtime_has_no_legacy_transport_or_zero_sentinel() -> None:
    runtime_paths = (
        "core/telegram/gateway.py",
        "apps/telegram_delivery_worker/main.py",
        "apps/telegram_update_worker/main.py",
        "apps/api/routers/v1/telegram_webhook.py",
        "core/telegram/command_replies.py",
    )
    source = "\n".join((ROOT / relative).read_text(encoding="utf-8") for relative in runtime_paths)

    for forbidden in (
        "sendRichMessage",
        "getUpdates",
        "telegram_poller",
        "run_telegram_poller.py",
        "AlertQueue",
        "core.alerts.queue",
        "alerts_queue_enabled",
        "run_drain_loop",
    ):
        assert forbidden not in source
    assert re.search(r"\bmessage_id\s*=\s*0\b", source) is None

    # Telegram workers expose Prometheus liveness (core/worker_metrics.py) and
    # durable PostgreSQL liveness (core/worker_liveness.py, issue #176); Redis
    # heartbeat compatibility paths remain physically absent. `core.worker_heartbeat`
    # was never a real module — it is a reserved, forbidden dotted path from an
    # earlier "safety-first" branch's Redis-backed heartbeat shim that was never
    # merged; keep it unimportable so it cannot be reintroduced under that name.
    for relative in (
        "apps/telegram_delivery_worker/main.py",
        "apps/telegram_update_worker/main.py",
    ):
        worker_source = (ROOT / relative).read_text(encoding="utf-8")
        imported = _imports(ast.parse(worker_source, filename=relative))
        assert "core.worker_heartbeat" not in imported
        assert not any(module == "redis" or module.startswith("redis.") for module in imported)
        assert "redis_heartbeat_loop" not in worker_source


def test_legacy_telegram_sources_are_physically_absent() -> None:
    retired_sources = (
        "core/telegram/alert_dispatcher.py",
        "core/telegram/bot_handler.py",
        "core/telegram/client.py",
        "core/telegram/digest_renderer.py",
        "core/telegram/messaging.py",
        "core/models/telegram/message_ref.py",
        "core/telegram/renderer.py",
        "core/telegram/handlers/ai_chat.py",
        "core/telegram/handlers/bulk.py",
        "core/telegram/handlers/creator.py",
        "core/telegram/handlers/draft_confirm.py",
        "core/telegram/handlers/spy.py",
    )
    assert not [relative for relative in retired_sources if (ROOT / relative).exists()]
    assert not list((ROOT / "core/alerts").glob("*.py"))

    retired_tests = (
        "tests/integration/test_alert_dispatcher_no_duplicate_send.py",
        "tests/integration/test_telegram_alert_dispatcher.py",
        "tests/integration/test_telegram_send_via_respx.py",
        "tests/unit/test_alert_dispatcher_fallback.py",
        "tests/unit/test_digest_renderer.py",
        "tests/unit/test_telegram_renderer.py",
        "tests/unit/test_tg_client_5xx_retry.py",
    )
    assert not [relative for relative in retired_tests if (ROOT / relative).exists()]


def test_environment_exposes_only_token_based_telegram_config() -> None:
    config_source = (ROOT / "core/config.py").read_text(encoding="utf-8")
    env_sources = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in (".env.example", ".env.local.example")
    )

    assert not (ROOT / "scripts/restore_secrets.py").exists()
    for retired in (
        "vision_settings",
        "telegram_settings",
        "forum_warning_thread_id",
        "forum_stop_thread_id",
        "forum_enable_thread_id",
        "forum_ops_thread_id",
        "forum_digest_thread_id",
        "TELEGRAM_CHAT_ID",
        "ALERTS_QUEUE_ENABLED",
        "telegram_chat_id: str",
    ):
        assert retired not in config_source
        assert retired not in env_sources


def test_telegram_source_tree_has_no_rich_polling_queue_or_table_path() -> None:
    source_paths = [
        *_python_files("core/telegram"),
        *_python_files("apps/telegram_delivery_worker"),
        *_python_files("apps/telegram_update_worker"),
    ]
    sources = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    fixture_source = (ROOT / "tests/integration/conftest.py").read_text(encoding="utf-8")

    for forbidden in (
        "sendRichMessage",
        "getUpdates",
        "TelegramBotClient",
        "<table",
        "core.alerts",
    ):
        assert forbidden not in sources
        assert forbidden not in fixture_source


def test_active_webhook_router_accepts_only_opaque_action_callbacks() -> None:
    router_path = ROOT / "core/telegram/handlers/router.py"
    router_source = router_path.read_text(encoding="utf-8")
    imported = _imports(ast.parse(router_source, filename=str(router_path)))

    assert "core.telegram.handlers.draft_confirm" not in imported
    assert "core.telegram.handlers.bulk" not in imported
    assert "core.telegram.handlers.creator" not in imported
    assert "handle_plan_run_callback" not in router_source
    assert "handle_enable_reco_callback" not in router_source
    assert "handle_dis_callback" not in router_source
    assert "_LEGACY_RAW_ID_CALLBACKS" not in router_source
    assert "spawn_ai_chat" not in router_source
    assert "draft_inline_keyboard" not in router_source
    assert 'if action == "a"' in router_source
