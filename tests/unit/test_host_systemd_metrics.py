from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import pytest

from scripts.host_metrics import HostMetricsError, HostMetricStore

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_writer_atomically_persists_failure_success_age_and_recovery(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "textfile"
    state_dir = tmp_path / "state"
    store = HostMetricStore(
        output_dir=output_dir,
        state_dir=state_dir,
        enforce_root=False,
    )

    store.record(
        "pgbackrest_full",
        "started",
        timestamp_seconds=100,
        boot_time_seconds=50,
    )
    store.record(
        "pgbackrest_full",
        "success",
        timestamp_seconds=110,
        boot_time_seconds=50,
    )
    store.record(
        "pgbackrest_full",
        "started",
        timestamp_seconds=200,
        boot_time_seconds=50,
    )
    store.record(
        "pgbackrest_full",
        "failure",
        timestamp_seconds=205,
        boot_time_seconds=50,
    )
    store.record(
        "pgbackrest_full",
        "started",
        timestamp_seconds=210,
        boot_time_seconds=50,
    )
    store.record(
        "pgbackrest_full",
        "success",
        timestamp_seconds=220,
        boot_time_seconds=50,
    )

    state = json.loads((state_dir / "pgbackrest_full.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"
    assert state["last_start_timestamp_seconds"] == 210
    assert state["last_completion_boot_time_seconds"] == 50
    assert state["last_success_timestamp_seconds"] == 220
    assert state["last_failure_timestamp_seconds"] == 205
    assert state["last_recovery_timestamp_seconds"] == 220
    assert state["last_duration_seconds"] == 10
    assert state["recovery_total"] == 1

    rendered = (output_dir / "fb-agent-host-operations.prom").read_text(encoding="utf-8")
    assert 'fb_agent_host_operation_status{operation="pgbackrest_full"} 1' in rendered
    assert (
        'fb_agent_host_operation_last_success_timestamp_seconds{operation="pgbackrest_full"} 220'
    ) in rendered
    assert (
        'fb_agent_host_operation_last_failure_timestamp_seconds{operation="pgbackrest_full"} 205'
    ) in rendered
    assert (
        'fb_agent_host_operation_last_completion_boot_time_seconds{operation="pgbackrest_full"} 50'
    ) in rendered
    assert ('fb_agent_host_operation_recoveries_total{operation="pgbackrest_full"} 1') in rendered
    assert stat.S_IMODE((state_dir / "pgbackrest_full.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((output_dir / "fb-agent-host-operations.prom").stat().st_mode) == 0o644
    assert not list(output_dir.glob(".fb-agent-host-operations.prom.*"))


def test_writer_rejects_non_root_cli_and_symlinked_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 12345)
    with pytest.raises(HostMetricsError, match="must run as root"):
        HostMetricStore(
            output_dir=tmp_path / "output",
            state_dir=tmp_path / "state",
        )

    real_state = tmp_path / "real-state"
    real_state.mkdir()
    state_link = tmp_path / "state-link"
    state_link.symlink_to(real_state, target_is_directory=True)
    store = HostMetricStore(
        output_dir=tmp_path / "output",
        state_dir=state_link,
        enforce_root=False,
    )
    with pytest.raises(HostMetricsError, match="symlinked directory"):
        store.record("restore_drill", "started", timestamp_seconds=1)


def test_every_host_operation_has_script_and_systemd_failure_coverage() -> None:
    expected_units = {
        "fb-agent-pgbackrest-full.service": "pgbackrest_full",
        "fb-agent-pgbackrest-diff.service": "pgbackrest_diff",
        "fb-agent-restore-drill.service": "restore_drill",
        "fb-agent-desktop-heal.service": "desktop_healer",
        "fb-agent-release-reconcile.service": "release_boot_reconcile",
    }
    for unit, operation in expected_units.items():
        source = _source(f"deploy/systemd/{unit}")
        assert f"OnFailure=fb-agent-host-operation-failed@{operation}.service" in source

    for script, operation in {
        "scripts/pgbackrest-admin.sh": "pgbackrest_full",
        "scripts/pgbackrest-restore-drill.sh": "restore_drill",
        "scripts/platform-desktop-heal.sh": "desktop_healer",
        "scripts/reconcile-platform-release.sh": "release_reconcile",
    }.items():
        source = _source(script)
        assert "host_metrics.py" in source
        assert operation in source
        assert "record_host_metric" in source

    failure_unit = _source("deploy/systemd/fb-agent-host-operation-failed@.service")
    assert "/usr/local/libexec/fb-agent-host-metrics/host_metrics.py" in failure_unit
    assert "--operation %i --outcome failure" in failure_unit


def test_release_rollback_uses_shared_writer_and_has_explicit_recovery() -> None:
    state = _source("scripts/release-state.py")
    reconcile = _source("scripts/reconcile-platform-release.sh")

    assert 'record_host_operation("release_rollback", "failure")' in state
    assert "fb_agent_release_rollback_failed" not in state
    assert "record_host_metric release_rollback success" in reconcile
    assert "rollback-failed.json" in reconcile


def test_reconciliation_dry_run_never_records_success_or_rollback_recovery() -> None:
    reconcile = _source("scripts/reconcile-platform-release.sh")

    metric_gate = reconcile.index('if [[ "$DRY_RUN" != true ]]; then')
    metric_start = reconcile.index("HOST_METRIC_STARTED=true", metric_gate)
    validation = reconcile.index(
        '[[ "$DEADLINE_SECONDS" =~ ^[0-9]+$ ]]',
        metric_start,
    )
    assert metric_gate < metric_start < validation
    assert (
        reconcile[metric_gate:validation].count(
            'record_host_metric "$HOST_METRIC_OPERATION" started'
        )
        == 1
    )
    assert 'if [[ "$HOST_METRIC_STARTED" == true ]]; then' in reconcile


def test_prometheus_rules_cover_host_failure_staleness_and_overdue_proofs() -> None:
    rules = _source("deploy/monitoring/prometheus/rules/fb-agent.yml")

    for alert in (
        "FBPgBackRestBackupFailed",
        "FBPgBackRestBackupStale",
        "FBPgBackRestFullBackupStale",
        "FBPgBackRestDiffBackupStale",
        "FBRestoreDrillFailed",
        "FBRestoreDrillOverdue",
        "FBDesktopHealerFailed",
        "FBDesktopHealerMetricsStale",
        "FBReleaseReconciliationFailed",
        "FBBootReconciliationMetricsAbsent",
        "FBReleaseRollbackFailed",
        "FBHostCriticalOperationRecoveredAfterFailure",
        "FBNodeExporterDown",
        "FBMonitoringNodeExporterDown",
        "FBExpectedApplicationHostInventoryAbsent",
        "FBNodeExporterSeriesAbsent",
        "FBHostOperationMetricsAbsent",
    ):
        assert f"- alert: {alert}" in rules
    assert 'node_textfile_scrape_error{job="node",role="application"} == 1' in rules
    assert "fb_agent_host_operation_last_success_timestamp_seconds" in rules
    assert "fb_agent_host_operation_last_failure_timestamp_seconds" in rules
    assert "fb_agent_host_operation_recoveries_total" in rules
    assert "fb_agent_host_operation_last_completion_boot_time_seconds" in rules
    assert "present_over_time" not in rules
    assert 'up{job="expected-application-host",role="application"}' in rules
    assert 'up{job="node",role="application"}' in rules
    assert 'up{job="node",role="monitoring"}' in rules
    assert "max(node_boot_time_seconds)" not in rules
    assert "on(node, instance)" in rules
    assert (
        'up{job="node",role="application"} == 1\n'
        "          )\n"
        "          unless on(node, instance)\n"
        "          count by (node, instance) (\n"
        '            fb_agent_host_operation_status{role="application"}'
    ) in rules
    assert rules.count("and on(node, instance, operation)") >= 3

    host_metric_names = set(re.findall(r"\b(fb_agent_host_operation_[a-z_]+)", rules))
    for metric_name in host_metric_names:
        selectors = re.findall(
            rf"\b{metric_name}(?:\{{[^}}]*\}})?",
            rules,
        )
        assert selectors
        assert all('role="application"' in selector for selector in selectors)

    boot_rule = rules.split(
        "- alert: FBBootReconciliationMetricsAbsent",
        maxsplit=1,
    )[1].split("- alert: FBReleaseRollbackFailed", maxsplit=1)[0]
    assert boot_rule.count('node_boot_time_seconds{job="node",role="application"}') >= 4
    assert (
        "!= on(node, instance)\n"
        "            fb_agent_host_operation_last_completion_boot_time_seconds"
    ) in boot_rule

    diff_rule = rules.split(
        "- alert: FBPgBackRestDiffBackupStale",
        maxsplit=1,
    )[1].split("- alert: FBRestoreDrillFailed", maxsplit=1)[0]
    assert "216000" in diff_rule
    assert "129600" not in diff_rule
    assert 'operation="pgbackrest_full"' in diff_rule


def test_prometheus_has_durable_role_aware_application_inventory() -> None:
    prometheus = _source("deploy/monitoring/prometheus/prometheus.yml")
    alloy = _source("deploy/monitoring/alloy/agent.alloy")
    inventory = json.loads(_source("deploy/monitoring/prometheus/targets/application-hosts.json"))
    rule_tests = _source("deploy/monitoring/prometheus/tests/host-operations.test.yml")
    validator = _source("scripts/validate-platform-configs.sh")

    assert "job_name: expected-application-host" in prometheus
    assert "application-hosts.json" in prometheus
    assert "action: drop" in prometheus
    assert "role: monitoring" in prometheus
    assert inventory == [
        {
            "targets": ["localhost:9090"],
            "labels": {
                "node": "fb-agent-app-1",
                "role": "application",
            },
        }
    ]
    application_scrape = alloy.split(
        'prometheus.scrape "application_host"',
        maxsplit=1,
    )[1].split("prometheus.remote_write", maxsplit=1)[0]
    assert application_scrape.count('"role"        = "application"') == 2
    assert "mixed topology" in rule_tests
    assert "eval_time: 25h" in rule_tests
    assert "FBNodeExporterSeriesAbsent" in rule_tests
    assert "test rules /etc/prometheus/tests/host-operations.test.yml" in validator


def test_all_node_exporters_mount_the_root_owned_textfile_collector() -> None:
    for compose_path in (
        "deploy/monitoring/docker-compose.agent.yml",
        "deploy/monitoring/docker-compose.monitoring.yml",
    ):
        compose = _source(compose_path)
        assert "--collector.textfile.directory=/var/lib/node_exporter/textfile_collector" in compose
        assert (
            "/var/lib/node_exporter/textfile_collector:/var/lib/node_exporter/textfile_collector:ro"
        ) in compose
