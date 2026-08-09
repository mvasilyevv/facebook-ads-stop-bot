from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/deploy.yml"


def _job_block(text: str, job: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        text,
    )
    assert match is not None, f"missing workflow job {job}"
    return match.group("body")


def test_registry_permissions_are_job_scoped() -> None:
    text = WORKFLOW.read_text()
    pre_jobs = text.split("\njobs:\n", maxsplit=1)[0]
    assert "packages: write" not in pre_jobs

    for job in ("build-base", "build", "build-desktop"):
        assert "packages: write" in _job_block(text, job)
    for job in ("release-manifest", "deploy"):
        assert "packages: read" in _job_block(text, job)

    for job in ("test", "web-test", "ui-evidence", "platform-config"):
        assert "packages:" not in _job_block(text, job)


def test_shellcheck_covers_every_supported_shell_entrypoint() -> None:
    platform_job = _job_block(WORKFLOW.read_text(encoding="utf-8"), "platform-config")
    shellcheck_step = platform_job.split(
        "- name: ShellCheck release and restore automation",
        maxsplit=1,
    )[1]
    for script in sorted((ROOT / "scripts").glob("*.sh")):
        assert f"scripts/{script.name}" in shellcheck_step, script.name


def test_ci_full_pytest_uses_complete_test_only_secret_contract() -> None:
    test_job = _job_block(WORKFLOW.read_text(encoding="utf-8"), "test")
    pytest_step = test_job.split("- name: pytest — полный прогон", maxsplit=1)[1]

    key_match = re.search(r"(?m)^\s+ENCRYPTION_KEY:\s+(\S+)$", pytest_step)
    verify_match = re.search(
        r"(?m)^\s+ENCRYPTION_KEY_VERIFY:\s+(\S+)$",
        pytest_step,
    )
    session_match = re.search(
        r"(?m)^\s+TMA_SESSION_SECRET:\s+(\S+)$",
        pytest_step,
    )
    assert key_match is not None
    assert verify_match is not None
    assert session_match is not None
    assert len(session_match.group(1)) >= 32

    from cryptography.fernet import Fernet

    assert (
        Fernet(key_match.group(1).encode()).decrypt(verify_match.group(1).encode())
        == b"encryption_key_verify_v1"
    )


def test_every_published_image_has_provenance_and_sbom() -> None:
    text = WORKFLOW.read_text()
    build_steps = text.count("uses: docker/build-push-action@")
    assert build_steps == 4
    assert text.count("provenance: mode=max") == build_steps
    assert text.count("sbom: true") == build_steps


def test_production_workflow_does_not_publish_mutable_image_tags() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert not re.search(
        r"(?m)^\s+\$\{\{ env\.IMAGE_BASE \}\}[^\n]*:latest\s*$",
        text,
    )
    assert "docker-image://${{ env.IMAGE_BASE }}-python-base:${{ github.sha }}" in text
    assert "Resolve all production images to digests" in text
    assert "./scripts/create-release-manifest.sh" in text


def test_ui_evidence_is_a_release_gate() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    evidence = _job_block(text, "ui-evidence")
    build_base = _job_block(text, "build-base")

    assert "pnpm install --frozen-lockfile" in evidence
    assert "playwright install --with-deps chromium firefox webkit" in evidence
    assert "build-storybook" in evidence
    assert "test:storybook" in evidence
    assert "test:e2e" in evidence
    assert "needs: [test, web-test, ui-evidence, platform-config]" in build_base


def test_pull_requests_run_verification_without_publishing() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    triggers = text.split("\njobs:\n", maxsplit=1)[0]

    assert "pull_request:\n    branches: [main]" in triggers
    for job in ("test", "web-test", "ui-evidence", "platform-config"):
        assert "github.event_name != 'pull_request'" not in _job_block(text, job)
    for job in ("build-base", "build", "build-desktop", "release-manifest"):
        assert "if: github.event_name != 'pull_request'" in _job_block(text, job)

    deploy = _job_block(text, "deploy")
    assert "github.ref == 'refs/heads/main'" in deploy


def test_workspace_makes_build_script_policy_explicit() -> None:
    workspace = (ROOT / "pnpm-workspace.yaml").read_text(encoding="utf-8")

    assert "allowBuilds:" in workspace
    assert '  "@tailwindcss/oxide": true' in workspace
    assert "  esbuild: true" in workspace
    assert "  msw: false" in workspace
    assert "onlyBuiltDependencies:" not in workspace
    assert "ignoredBuiltDependencies:" not in workspace


def test_external_docker_bases_are_digest_pinned() -> None:
    direct_base_files = (
        "docker/Dockerfile.python-base",
        "docker/Dockerfile.browser-agent",
        "docker/Dockerfile.frontend",
        "docker/Dockerfile.mini-app",
        "deploy/vision-webtop/Dockerfile",
    )
    digest = re.compile(r"@sha256:[0-9a-f]{64}(?:\s|$)")
    for relative in direct_base_files:
        lines = [
            line for line in (ROOT / relative).read_text().splitlines() if line.startswith("FROM ")
        ]
        assert lines
        assert all(digest.search(line) for line in lines), (relative, lines)

    postgres = (ROOT / "docker/Dockerfile.postgres").read_text()
    assert re.search(
        r"(?m)^ARG POSTGRES_BASE_IMAGE=postgres:\S+@sha256:[0-9a-f]{64}$",
        postgres,
    )

    # COPY --from can pull a remote image just like FROM.  It must not escape
    # the immutable-base policy (the uv installer used to reference :latest).
    dockerfiles = tuple((ROOT / "docker").rglob("Dockerfile*")) + tuple(
        (ROOT / "deploy").rglob("Dockerfile*")
    )
    for dockerfile in dockerfiles:
        source = dockerfile.read_text(encoding="utf-8")
        local_stages = {
            match.group(1).lower()
            for match in re.finditer(r"(?im)^FROM\s+\S+\s+AS\s+(\S+)\s*$", source)
        }
        for match in re.finditer(r"(?im)^COPY\s+--from=(\S+)", source):
            copy_source = match.group(1)
            if copy_source.lower() in local_stages or copy_source.isdigit():
                continue
            assert re.fullmatch(r"\S+@sha256:[0-9a-f]{64}", copy_source), (
                dockerfile.relative_to(ROOT),
                copy_source,
            )


def test_application_host_exports_host_and_container_metrics() -> None:
    compose = (ROOT / "deploy/monitoring/docker-compose.agent.yml").read_text()
    alloy = (ROOT / "deploy/monitoring/alloy/agent.alloy").read_text()
    runtime = (ROOT / "scripts/platform-alloy-agent.sh").read_text()

    assert "NODE_EXPORTER_IMAGE" in compose
    assert "CADVISOR_IMAGE" in compose
    assert '"__address__" = "node-exporter:9100"' in alloy
    assert '"__address__" = "cadvisor:8080"' in alloy
    assert '"node"        = sys.env("NODE_NAME")' in alloy
    assert "pull alloy-agent node-exporter cadvisor" in runtime


def test_runtime_exports_instrumented_application_traces_through_alloy() -> None:
    app = (ROOT / "deploy/compose/docker-compose.app.yml").read_text()
    agent = (ROOT / "deploy/monitoring/alloy/agent.alloy").read_text()
    readme = (ROOT / "deploy/monitoring/README.md").read_text()
    dependencies = (ROOT / "pyproject.toml").read_text()

    assert "OTEL_EXPORTER_OTLP_ENDPOINT: http://alloy-agent:4317" in app
    assert 'otelcol.receiver.otlp "applications"' in agent
    assert 'otelcol.exporter.otlphttp "central_tempo"' in agent
    assert 'sys.env("TEMPO_OTLP_HTTP_URL")' in agent
    for package in (
        "opentelemetry-instrumentation-fastapi",
        "opentelemetry-instrumentation-httpx",
        "opentelemetry-instrumentation-sqlalchemy",
        "opentelemetry-instrumentation-grpc",
    ):
        assert package in dependencies
    assert "Telegram bot-token path segments" in readme
