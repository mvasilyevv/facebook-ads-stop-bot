from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFY_WORKFLOW = ROOT / ".github/workflows/verify.yml"
IMAGE_WORKFLOW = ROOT / ".github/workflows/publish-images.yml"
RELEASE_WORKFLOW = ROOT / ".github/workflows/release.yml"
LOCKFILE = ROOT / "pnpm-lock.yaml"


def _job_block(text: str, job: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        text,
    )
    assert match is not None, f"missing workflow job {job}"
    return match.group("body")


def _job_if_expression(text: str, job: str) -> str:
    match = re.search(
        r"(?ms)^    if:\s*(?P<expression>.*?)(?=^    [a-zA-Z0-9_-]+:|\Z)",
        _job_block(text, job),
    )
    assert match is not None, f"missing job-level if for workflow job {job}"
    return " ".join(match.group("expression").split())


def test_registry_permissions_are_job_scoped() -> None:
    images = IMAGE_WORKFLOW.read_text()
    release = RELEASE_WORKFLOW.read_text()
    pre_jobs = images.split("\njobs:\n", maxsplit=1)[0]
    assert "packages: write" not in pre_jobs

    for job in ("base", "app", "desktop"):
        assert "packages: write" in _job_block(images, job)
    assert "packages:" not in _job_block(images, "manifest")
    assert "packages: read" in _job_block(release, "deploy")

    verify = VERIFY_WORKFLOW.read_text()
    for job in ("backend", "frontend", "ui-evidence", "platform"):
        assert "packages:" not in _job_block(verify, job)


def test_app_image_publishing_bounds_ghcr_auth_pressure_and_retries() -> None:
    app = _job_block(IMAGE_WORKFLOW.read_text(encoding="utf-8"), "app")

    assert "fail-fast: false\n      max-parallel: 2" in app
    login = app.split("- name: GHCR login with bounded retry", maxsplit=1)[1].split(
        "- name: Probe immutable tag", maxsplit=1
    )[0]
    assert "for attempt in 1 2 3" in login
    assert 'docker login "$REGISTRY"' in login
    assert "--password-stdin" in login
    assert 'if [ "$attempt" -eq 3 ]' in login
    assert "exit 1" in login
    assert "continue-on-error" not in login


def test_shellcheck_covers_every_supported_shell_entrypoint() -> None:
    platform_job = _job_block(VERIFY_WORKFLOW.read_text(encoding="utf-8"), "platform")
    shellcheck_step = platform_job.split(
        "- name: ShellCheck supported shell automation",
        maxsplit=1,
    )[1]
    assert "find scripts -maxdepth 1 -type f -name '*.sh' -print0" in shellcheck_step
    assert "xargs -0 shellcheck" in shellcheck_step


def test_ci_installs_nothing_from_ubuntu_package_mirrors() -> None:
    """Проверка не зависит от зеркал Ubuntu: они виснут молча и без таймаута.

    19.08.2026 `apt-get update` в джобе platform замолчал на девять с половиной
    минут: azure.archive.ubuntu.com перестал отвечать, apt ушёл на
    archive.ubuntu.com и там застыл. Джобу срубил timeout-minutes, релиз не
    дошёл до выкатки. Ставились ровно два пакета: shellcheck, который и так
    входит в образ раннера той же версией, и ripgrep, которого не зовёт никто.

    Инструмент берётся из образа раннера или из закреплённого по digest
    контейнера — как это делает scripts/preflight.sh. Пакет, которого в образе
    нет, тянется с явной версией и собственным таймаутом, а не `apt-get update`.
    """
    apt_call = re.compile(r"\bapt(?:-get)?\s+(?:update|install)\b|\badd-apt-repository\b")

    for workflow in (VERIFY_WORKFLOW, IMAGE_WORKFLOW, RELEASE_WORKFLOW):
        source = workflow.read_text(encoding="utf-8")
        # Комментарии описывают инцидент и не выполняются — гард смотрит на код.
        executable = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert apt_call.search(executable) is None, (
            f"{workflow.name} ставит пакеты через apt — это зависимость от зеркал "
            "Ubuntu, которая уже дважды вешала джобу до предела времени"
        )


def test_every_external_workflow_dependency_is_immutable() -> None:
    """Внешнее в CI закреплено неизменяемо: action — по SHA, образ — по digest.

    Тег переезжает без нашего ведома, и проверка начинает мерить не тот код,
    который мы читаем. Локальные `uses: ./…` — наш же файл в этом коммите,
    им закрепление не нужно.
    """
    action_sha = re.compile(r"@[0-9a-f]{40}$")

    for workflow in (VERIFY_WORKFLOW, IMAGE_WORKFLOW, RELEASE_WORKFLOW):
        source = workflow.read_text(encoding="utf-8")

        for match in re.finditer(r"(?m)^\s*uses:\s*(\S+)", source):
            ref = match.group(1)
            if ref.startswith("./"):
                continue
            assert action_sha.search(ref), f"{workflow.name}: action не закреплён по SHA — {ref}"

        for match in re.finditer(r"(?m)^\s*image:\s*(\S+)", source):
            ref = match.group(1)
            assert "@sha256:" in ref, f"{workflow.name}: образ не закреплён по digest — {ref}"

        # `docker run` тянет образ мимо ключа image: и обязан быть закреплён так же.
        for block in re.finditer(r"docker run(?P<body>(?:.|\n)*?)(?=\n\s*- name:|\n\n|\Z)", source):
            assert "@sha256:" in block.group("body"), (
                f"{workflow.name}: docker run без digest — образ может переехать между прогонами"
            )


def test_ci_full_pytest_uses_complete_test_only_secret_contract() -> None:
    test_job = _job_block(VERIFY_WORKFLOW.read_text(encoding="utf-8"), "backend")

    key_match = re.search(r"(?m)^\s+ENCRYPTION_KEY:\s+(\S+)$", test_job)
    verify_match = re.search(
        r"(?m)^\s+ENCRYPTION_KEY_VERIFY:\s+(\S+)$",
        test_job,
    )
    session_match = re.search(
        r"(?m)^\s+TMA_SESSION_SECRET:\s+(\S+)$",
        test_job,
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


def test_backend_ci_uses_the_production_frozen_dependency_graph() -> None:
    test_job = _job_block(VERIFY_WORKFLOW.read_text(encoding="utf-8"), "backend")

    assert 'python -m pip install "uv==0.9.18"' in test_job
    assert "uv sync --frozen --extra dev" in test_job
    assert 'echo "$GITHUB_WORKSPACE/.venv/bin" >> "$GITHUB_PATH"' in test_job
    assert 'pip install -e ".[dev]"' not in test_job
    assert "pytest tests/ --timeout=30 -q" in test_job
    assert "pytest tests/ -x" not in test_job


def test_developer_and_ci_python_commands_do_not_write_repo_bytecode() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    guide = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    assert "PY := PYTHONDONTWRITEBYTECODE=1 $(VENV_BIN)/python" in makefile
    assert "PYTEST := PYTHONDONTWRITEBYTECODE=1 $(VENV_BIN)/pytest" in makefile
    assert "PYTHONDONTWRITEBYTECODE=1 pytest tests/unit -q" in guide
    assert "PYTHONDONTWRITEBYTECODE=1 python scripts/export_openapi.py" in guide

    for workflow, jobs in (
        (VERIFY_WORKFLOW, ("backend", "platform")),
        (IMAGE_WORKFLOW, ("plan", "base", "manifest")),
        (
            RELEASE_WORKFLOW,
            ("bootstrap-source-preflight", "control-bundle", "docker-rehearsal", "deploy"),
        ),
    ):
        source = workflow.read_text(encoding="utf-8")
        for job in jobs:
            assert 'PYTHONDONTWRITEBYTECODE: "1"' in _job_block(source, job)


def test_every_published_image_has_provenance_and_sbom() -> None:
    text = IMAGE_WORKFLOW.read_text()
    build_steps = text.count("uses: docker/build-push-action@")
    assert build_steps == 4
    assert text.count("provenance: mode=max") == build_steps
    assert text.count("sbom: true") == build_steps


def test_production_workflow_does_not_publish_mutable_image_tags() -> None:
    text = IMAGE_WORKFLOW.read_text(encoding="utf-8")

    assert not re.search(
        r"(?m)^\s+\$\{\{ env\.IMAGE_REPOSITORY \}\}[^\n]*:latest\s*$",
        text,
    )
    assert "tags: ${{ steps.probe.outputs.candidate }}" in text
    assert "python3 scripts/ci_image_plan.py matrix --group app" in text
    assert "python3 scripts/ci_image_plan.py manifest" in text
    assert '--release-id "${{ inputs.release_id }}"' in text
    assert "github.sha }}" not in "\n".join(
        line for line in text.splitlines() if line.lstrip().startswith("tags:")
    )


def test_ui_evidence_is_a_release_gate() -> None:
    verify = VERIFY_WORKFLOW.read_text(encoding="utf-8")
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    evidence = _job_block(verify, "ui-evidence")

    assert "pnpm install --frozen-lockfile" in evidence
    # Браузеры приходят из образа, закреплённого по digest: установка через
    # `playwright install-deps` зовёт apt и 19.08.2026 повесила джобу на 29 минут.
    # Версия образа обязана совпадать с клиентом из lock-файла — разъехавшись,
    # они дают несовпадение сборок браузера прямо посреди приёмочного прогона.
    image = re.search(
        r"image: mcr\.microsoft\.com/playwright:v(?P<version>[\d.]+)-noble@sha256:[0-9a-f]{64}",
        evidence,
    )
    assert image is not None, "джоба не закрепляет образ Playwright по digest"
    locked = re.search(r"(?m)^  '@playwright/test@(?P<version>[\d.]+)':$", LOCKFILE.read_text())
    assert locked is not None
    assert image.group("version") == locked.group("version"), (
        f"образ Playwright v{image.group('version')} разошёлся с клиентом "
        f"{locked.group('version')} из pnpm-lock.yaml"
    )
    assert "--ipc=host" in evidence, "Chromium без общей IPC падает по разделяемой памяти"
    assert "HOME: /root" in evidence, (
        "без своего HOME Firefox не стартует: каталог Actions принадлежит pwuser из образа"
    )
    executable = "\n".join(
        line for line in evidence.splitlines() if not line.lstrip().startswith("#")
    )
    assert "playwright install" not in executable
    assert "build-storybook" in evidence
    assert "test:storybook" in evidence
    assert "test:e2e" in evidence
    assert "uses: ./.github/workflows/verify.yml" in _job_block(release, "verify")
    assert "needs: [verify, bootstrap-source-preflight]" in _job_block(release, "images")


def test_pull_requests_run_verification_without_publishing() -> None:
    verify = VERIFY_WORKFLOW.read_text(encoding="utf-8")
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    images = IMAGE_WORKFLOW.read_text(encoding="utf-8")
    triggers = verify.split("\njobs:\n", maxsplit=1)[0]

    assert "pull_request:\n    branches: [main]" in triggers
    assert "push:" not in triggers
    assert "workflow_call:" in triggers
    assert "workflow_call:" in images.split("\njobs:\n", maxsplit=1)[0]
    assert "pull_request:" not in RELEASE_WORKFLOW.read_text().split("\njobs:\n", 1)[0]
    deploy = _job_block(release, "deploy")
    assert "github.event_name == 'workflow_dispatch'" in deploy
    assert "github.ref == 'refs/heads/main'" in deploy
    assert "vars.CI_DEPLOY_ENABLED == 'true'" in deploy


def test_workflow_syntax_openapi_and_control_bundle_are_release_gates() -> None:
    verify = VERIFY_WORKFLOW.read_text(encoding="utf-8")
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "actionlint" in _job_block(verify, "platform")
    scripts = package["scripts"]
    assert "pnpm --dir frontend exec prettier" in scripts["format:openapi"]
    assert "pnpm --dir frontend exec prettier" in scripts["format:api"]
    assert "scripts/export_openapi.py" in scripts["export:openapi"]
    assert "pnpm run format:openapi" in scripts["export:openapi"]
    assert "pnpm run format:api" in scripts["gen:api"]
    assert scripts["sync:api"] == "pnpm run export:openapi && pnpm run gen:api"
    assert "pnpm run sync:api" in _job_block(verify, "frontend")
    assert (
        "git diff --exit-code -- frontend/openapi.json packages/shared/src/api/generated.ts"
        in verify
    )
    assert "sync-api-contract: install-backend" in makefile
    assert 'PYTHON="$(abspath $(VENV_BIN)/python)" pnpm run sync:api' in makefile
    assert "scripts/fbctl bundle" in _job_block(release, "control-bundle")
    assert "--release-manifest release-inputs/release.json" in release
    assert "rsync" not in release
    assert "scripts/fbctl publish" in _job_block(release, "deploy")


def test_grpc_stub_generation_uses_one_locked_project_toolchain() -> None:
    workflow = _job_block(VERIFY_WORKFLOW.read_text(encoding="utf-8"), "backend")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    generator = (ROOT / "scripts/generate_grpc_stubs.py").read_text(encoding="utf-8")

    assert "python -B scripts/generate_grpc_stubs.py" in workflow
    assert "grpc_tools.protoc" not in workflow
    proto_compile = makefile.split("proto-compile:", maxsplit=1)[1].split(
        "proto-watch:", maxsplit=1
    )[0]
    assert (
        proto_compile.strip()
        == "## Скомпилировать proto файлы в Python stubs\n\t$(PY) scripts/generate_grpc_stubs.py"
    )
    assert "$(RUFF)" not in proto_compile
    assert "grpc_tools.protoc" not in proto_compile
    assert (
        'sys.executable,\n                "-m",\n                "grpc_tools.protoc"' in generator
    )
    assert "tempfile.TemporaryDirectory" in generator
    assert '"from v1 import ", "from . import "' in generator


def test_pre_commit_respects_ruff_excludes_for_generated_grpc_stubs() -> None:
    hooks = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    assert "entry: .venv/bin/ruff check --force-exclude --fix" in hooks
    assert "entry: .venv/bin/ruff format --force-exclude" in hooks


def test_production_source_secret_is_only_consumed_by_manual_bootstrap() -> None:
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    deploy = _job_block(release, "deploy")
    routine = deploy.split("- name: Publish routine release", 1)[1].split(
        "- name: Bootstrap host and deploy first release", 1
    )[0]
    bootstrap = deploy.split("- name: Bootstrap host and deploy first release", 1)[1].split(
        "- name: Remove temporary GHCR credentials", 1
    )[0]

    assert "PROD_ENV_B64" not in release.split("  bootstrap-source-preflight:\n", 1)[0]
    assert "PROD_ENV_B64" not in routine
    assert "--source-env-stdin" not in routine
    assert "PROD_ENV_B64: ${{ secrets.PROD_ENV_B64 }}" in bootstrap
    assert "--source-env-stdin" in bootstrap
    assert "--project-known-legacy-source" not in routine
    assert "--project-known-legacy-source" in bootstrap
    assert "--migrate-existing-bootstrap-identity" not in routine
    assert "--migrate-existing-bootstrap-identity" in bootstrap
    assert "github.event_name == 'workflow_dispatch' && inputs.bootstrap" in bootstrap
    assert "/opt/fb-agent/shared/adoption-bundle-v1.json" in bootstrap
    assert "/opt/fb-agent/shared/desktop-profile-seed" in bootstrap
    assert "/opt/fb-agent/shared/vision-profile-seed" not in bootstrap
    assert "--provision-caddy" not in bootstrap


def test_existing_bootstrap_identity_is_validated_remotely_before_expensive_jobs() -> None:
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    preflight = _job_block(release, "bootstrap-source-preflight")
    images = _job_block(release, "images")

    assert "needs: verify" in preflight
    assert (
        "if: github.event_name == 'workflow_dispatch' && inputs.bootstrap && "
        "github.ref == 'refs/heads/main'"
    ) in preflight
    assert "environment: production" in preflight
    assert "PROD_ENV_B64: ${{ secrets.PROD_ENV_B64 }}" in preflight
    assert 'test -n "$PROD_ENV_B64"' in preflight
    assert "scripts/fbctl preflight-bundle" in preflight
    assert "--output fbctl-preflight.pyz" in preflight
    assert '--release-id "${{ github.sha }}"' in preflight
    assert "base64 --decode | scripts/fbctl bootstrap-remote-preflight" in preflight
    assert '--host "$DEPLOY_TARGET"' in preflight
    assert "--bundle fbctl-preflight.pyz" in preflight
    assert "--source-env-stdin" in preflight
    assert "--project-known-legacy-source" in preflight
    assert "--migrate-existing-bootstrap-identity" in preflight
    assert preflight.index("Build deterministic bootstrap preflight bundle") < preflight.index(
        "Configure pinned SSH host identity"
    )
    assert preflight.index("Configure pinned SSH host identity") < preflight.index(
        "Validate bootstrap source and existing host identity"
    )
    assert "StrictHostKeyChecking yes" in preflight
    assert "UserKnownHostsFile ~/.ssh/known_hosts" in preflight
    assert 'ssh-keygen -F "$DEPLOY_HOST" -f ~/.ssh/known_hosts' in preflight
    assert "set -x" not in preflight
    assert "GITHUB_OUTPUT" not in preflight
    assert "upload-artifact" not in preflight
    assert "cache" not in preflight
    assert "GHCR" not in preflight
    assert "docker login" not in preflight
    assert "release.json" not in preflight
    assert "source.env" not in preflight
    assert "base64 --decode >" not in preflight
    assert "needs: [verify, bootstrap-source-preflight]" in images
    assert "!cancelled()" in images
    assert "needs.verify.result == 'success'" in images
    assert "needs.bootstrap-source-preflight.result == 'success'" in images
    assert (
        "needs.bootstrap-source-preflight.result == 'skipped' &&\n"
        "            !(github.event_name == 'workflow_dispatch' && inputs.bootstrap)"
    ) in images


def test_skipped_bootstrap_ancestor_does_not_skip_release_gates() -> None:
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert _job_if_expression(release, "images") == (
        ">- ${{ !cancelled() && needs.verify.result == 'success' && ( "
        "needs.bootstrap-source-preflight.result == 'success' || ( "
        "needs.bootstrap-source-preflight.result == 'skipped' && "
        "!(github.event_name == 'workflow_dispatch' && inputs.bootstrap) ) ) }}"
    )
    assert _job_if_expression(release, "control-bundle") == (
        ">- ${{ !cancelled() && needs.images.result == 'success' }}"
    )
    assert _job_if_expression(release, "docker-rehearsal") == (
        ">- ${{ !cancelled() && needs.images.result == 'success' && "
        "needs.control-bundle.result == 'success' }}"
    )
    assert _job_if_expression(release, "deploy") == (
        ">- ${{ !cancelled() && github.event_name == 'workflow_dispatch' && "
        "github.ref == 'refs/heads/main' && vars.CI_DEPLOY_ENABLED == 'true' && "
        "needs.images.result == 'success' && needs.control-bundle.result == 'success' "
        "&& needs.docker-rehearsal.result == 'success' }}"
    )


def test_bootstrap_identity_migration_is_absent_from_routine_release_paths() -> None:
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    preflight = _job_block(release, "bootstrap-source-preflight")
    deploy = _job_block(release, "deploy")
    routine = deploy.split("- name: Publish routine release", 1)[1].split(
        "- name: Bootstrap host and deploy first release", 1
    )[0]
    bootstrap = deploy.split("- name: Bootstrap host and deploy first release", 1)[1].split(
        "- name: Remove temporary GHCR credentials", 1
    )[0]

    assert preflight.count("--migrate-existing-bootstrap-identity") == 1
    assert bootstrap.count("--migrate-existing-bootstrap-identity") == 1
    assert "--migrate-existing-bootstrap-identity" not in routine
    assert "bootstrap-remote-preflight" not in routine
    assert "PROD_ENV_B64" not in routine


def test_release_requires_real_single_slot_rehearsal_before_production() -> None:
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    rehearsal = _job_block(release, "docker-rehearsal")
    deploy = _job_block(release, "deploy")

    assert "needs: [images, control-bundle]" in rehearsal
    assert "packages: read" in rehearsal
    assert "tests.rehearsal.single_slot" in rehearsal
    assert "release-inputs/release.json" in rehearsal
    assert "FB_AGENT_REHEARSAL_ACK: single-slot" in rehearsal
    assert "timeout-minutes: 30" in rehearsal
    # Провал одного шарда не должен прятать остальные — иначе один отчёт
    # вместо пяти, и следующая причина всплывает только на следующем прогоне.
    assert "fail-fast: false" in rehearsal
    assert rehearsal.count("- case: ") == 5
    # Ни один шард не ждёт другого: у них нет общих ресурсов, а ожидание
    # умножает длительность релиза на число волн.
    parallel_limit = re.search(r"^      max-parallel: (\d+)$", rehearsal, re.MULTILINE)
    assert parallel_limit is not None
    assert int(parallel_limit.group(1)) >= rehearsal.count("- case: ")
    assert rehearsal.count("scenario: failpoints") == 4
    for index in range(4):
        assert (
            f"- case: fp{index}\n"
            "            scenario: failpoints\n"
            f"            shard_index: {index}\n"
            "            shard_count: 4"
        ) in rehearsal
    assert (
        "- case: acceptance\n"
        "            scenario: acceptance\n"
        "            shard_index: 0\n"
        "            shard_count: 1"
    ) in rehearsal
    assert '--scenario "${{ matrix.scenario }}"' in rehearsal
    assert '--shard-index "${{ matrix.shard_index }}"' in rehearsal
    assert '--shard-count "${{ matrix.shard_count }}"' in rehearsal
    assert "${{ runner.temp }}" not in rehearsal
    assert 'docker_config="$RUNNER_TEMP/fb-agent-docker-config-${{ matrix.case }}"' in rehearsal
    assert 'install -d -m 700 "$docker_config"' in rehearsal
    assert 'printf \'DOCKER_CONFIG=%s\\n\' "$docker_config" >> "$GITHUB_ENV"' in rehearsal
    assert rehearsal.index("Prepare explicit Docker credentials directory") < rehearsal.index(
        "Authenticate immutable release pulls"
    )
    elevated = rehearsal.split("sudo -n env -i", maxsplit=1)[1].split(
        "/usr/bin/python3", maxsplit=1
    )[0]
    assert "sudo -E" not in rehearsal
    assert set(re.findall(r"(?m)^\s+([A-Z][A-Z0-9_]*)=", elevated)) == {
        "DOCKER_CONFIG",
        "GITHUB_ACTIONS",
        "GITHUB_RUN_ID",
        "FB_AGENT_REHEARSAL_ACK",
        "PYTHONDONTWRITEBYTECODE",
    }
    assert 'GITHUB_RUN_ID="${GITHUB_RUN_ID}-${{ matrix.case }}"' in elevated
    assert "needs: [images, control-bundle, docker-rehearsal]" in deploy


def test_platform_validator_uses_only_the_zipapp_control_plane() -> None:
    wrapper = (ROOT / "scripts/validate-platform-configs.sh").read_text(encoding="utf-8")
    validator = (ROOT / "scripts/validate_platform_configs.py").read_text(encoding="utf-8")

    assert len(wrapper.splitlines()) <= 10
    assert 'exec python3 -B "$SCRIPT_DIR/validate_platform_configs.py"' in wrapper
    assert 'ROOT / "scripts/fbctl"' in validator
    assert '"schema": "fb-agent-release/v1"' in validator
    assert "inspect_bundle(bundle)" in validator
    assert '"config", "--quiet"' in validator
    assert "compileall" not in validator
    assert "py_compile" not in validator
    for compose in (
        "docker-compose.infra.yml",
        "docker-compose.jobs.yml",
        "docker-compose.app.yml",
        "docker-compose.desktop-agent.yml",
    ):
        assert compose in validator
    for retired in (
        "release-images.env",
        "render-production-runtime-env.sh",
        "deploy-lock-remote.sh",
        "deploy-platform-server.sh",
        "production-deploy.sh",
    ):
        assert retired not in validator


def test_platform_validator_leaves_worktree_and_python_cache_unchanged() -> None:
    import subprocess

    before_status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    before_caches = {
        path.relative_to(ROOT) for path in ROOT.rglob("__pycache__") if ".venv" not in path.parts
    }

    subprocess.run(
        [str(ROOT / "scripts/validate-platform-configs.sh")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )

    after_status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    after_caches = {
        path.relative_to(ROOT) for path in ROOT.rglob("__pycache__") if ".venv" not in path.parts
    }
    assert after_status == before_status
    assert after_caches == before_caches


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

    assert "NODE_EXPORTER_IMAGE" in compose
    assert "CADVISOR_IMAGE" in compose
    assert '"__address__" = "node-exporter:9100"' in alloy
    assert '"__address__" = "cadvisor:8080"' in alloy
    assert '"node"        = sys.env("NODE_NAME")' in alloy
    assert "restart: unless-stopped" in compose


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


def test_only_manual_release_deploys_so_push_runs_are_safe_to_cancel() -> None:
    """Отмена устаревшего прогона безопасна ровно пока push ничего не выкатывает.

    Прогоны от push отменяют друг друга: серия коммитов иначе строит очередь
    по полчаса. Если deploy когда-нибудь начнёт срабатывать на push, эта
    отмена начнёт обрывать выкатку на живой хост — тогда сначала меняется
    concurrency, и только потом условие job.
    """
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "github.event_name == 'workflow_dispatch'" in _job_if_expression(workflow, "deploy")
    assert "group: release-main-${{ github.event_name }}" in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'push' }}" in workflow


def test_called_verify_cannot_cancel_a_manual_release() -> None:
    """Неотменяемость ручного релиза обязана держаться и в вызываемом workflow.

    Release объявляет ручной запуск неотменяемым, но verify вызывается внутри
    него, и там github.workflow — имя вызывающего. Пока событие не входило в
    группу, ручная выкатка и прогон от push делили одну группу: любой merge в
    main отменял идущий релиз, а отмена после stop_runtime оставила бы
    production остановленным.
    """
    verify = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")

    assert (
        "group: verify-${{ github.workflow }}-${{ github.ref }}-${{ github.event_name }}" in verify
    )
    assert "cancel-in-progress: ${{ github.event_name != 'workflow_dispatch' }}" in verify
