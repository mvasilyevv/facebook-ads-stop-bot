import re
import subprocess
from pathlib import Path

import pytest

from scripts.ci_image_plan import (
    RUNTIME_MANIFEST_KEYS,
    SPECS,
    PlanError,
    _matches,
    build_matrix,
    compute_hashes,
    write_manifest,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for spec in SPECS.values():
        for relative in spec.exact:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"exact:{relative}\n", encoding="utf-8")
        for prefix in spec.prefixes:
            path = root / prefix / "input.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"prefix:{prefix}\n", encoding="utf-8")
        for pattern in spec.suffixes:
            path = root / pattern.replace("*", "runtime")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"suffix:{pattern}\n", encoding="utf-8")
    host_only = root / "fbctl/publish.py"
    host_only.parent.mkdir(parents=True, exist_ok=True)
    host_only.write_text("host release v1\n", encoding="utf-8")
    workflow = root / ".github/workflows/deploy.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: release v1\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    return root


def test_catalog_excludes_custom_postgres_and_covers_runtime_images() -> None:
    assert "postgres" not in SPECS
    assert "postgres" not in RUNTIME_MANIFEST_KEYS
    assert set(RUNTIME_MANIFEST_KEYS) == {
        "api",
        "workers",
        "frontend",
        "mini-app",
        "browser-agent",
        "vision-webtop",
    }


def test_python_image_copies_only_catalogued_runtime_inputs() -> None:
    dockerfile = (Path(__file__).resolve().parents[2] / "docker/Dockerfile.python-base").read_text(
        encoding="utf-8"
    )
    python_spec = SPECS["python-base"]

    assert "COPY . ." not in dockerfile
    assert "COPY run_*.py ./" in dockerfile
    for runtime_script in (
        "scripts/run-migrations-locked.py",
        "scripts/adopt-first-release.py",
        "scripts/adoption-receipt-status.py",
        "scripts/bootstrap-runtime-config.py",
        "scripts/bootstrap-vision-config.py",
        "scripts/check-database-contract.py",
        "scripts/configure-telegram-webhook.py",
    ):
        assert runtime_script in dockerfile
        assert runtime_script in python_spec.exact
    assert not _matches(python_spec, "fbctl/publish.py")
    assert _matches(python_spec, "docs/creatives/hooks.yaml")
    assert not _matches(python_spec, "docs/creatives/reports/source-video.mp4")


def test_host_only_changes_do_not_change_any_image_hash(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    before = compute_hashes(root)

    (root / "fbctl/publish.py").write_text("host release v2\n", encoding="utf-8")
    (root / ".github/workflows/deploy.yml").write_text("name: release v2\n", encoding="utf-8")

    assert compute_hashes(root) == before
    assert not _matches(SPECS["python-base"], "fbctl/publish.py")


def test_image_hashes_change_only_for_their_relevant_inputs(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    before = compute_hashes(root)
    source = root / "deploy/vision-webtop/input.txt"
    source.write_text("vision v2\n", encoding="utf-8")
    after = compute_hashes(root)

    assert after["vision-webtop"] != before["vision-webtop"]
    assert all(after[name] == before[name] for name in SPECS if name != "vision-webtop")


def test_python_base_change_invalidates_api_and_workers(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    before = compute_hashes(root)
    source = root / "core/input.txt"
    source.write_text("core v2\n", encoding="utf-8")
    after = compute_hashes(root)

    assert after["python-base"] != before["python-base"]
    assert after["api"] != before["api"]
    assert after["workers"] != before["workers"]
    assert after["frontend"] == before["frontend"]
    assert after["vision-webtop"] == before["vision-webtop"]


def test_database_contract_check_invalidates_python_images(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    before = compute_hashes(root)
    source = root / "scripts/check-database-contract.py"
    source.write_text("database contract v2\n", encoding="utf-8")
    after = compute_hashes(root)

    assert {name for name in SPECS if after[name] != before[name]} == {
        "python-base",
        "api",
        "workers",
    }


def test_matrix_uses_context_hash_tags(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    matrix = build_matrix(root, "app")

    assert {item["name"] for item in matrix["include"]} == {
        "api",
        "workers",
        "frontend",
        "mini-app",
        "browser-agent",
    }
    assert all(str(item["tag"]).startswith(f"{item['name']}-ctx-") for item in matrix["include"])
    assert all("postgres" not in str(item) for item in matrix["include"])


def test_manifest_uses_current_release_and_only_immutable_refs(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    for index, image_name in enumerate(RUNTIME_MANIFEST_KEYS, start=1):
        (refs / f"{image_name}.ref").write_text(
            f"ghcr.io/example/fb-agent-{image_name}@sha256:{index:064x}\n",
            encoding="utf-8",
        )
    output = tmp_path / "release.json"
    redis = "redis:7-alpine@sha256:" + "f" * 64

    write_manifest(refs, "release-42", output, redis)

    import json

    content = json.loads(output.read_text(encoding="utf-8"))
    assert content["schema"] == "fb-agent-release/v1"
    assert content["release_id"] == "release-42"
    assert "POSTGRES_IMAGE" not in content["images"]
    assert content["images"]["REDIS_IMAGE"] == redis
    assert set(content) == {"schema", "release_id", "images"}
    assert output.stat().st_mode & 0o777 == 0o600


def test_manifest_rejects_mutable_image_ref(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    for image_name in RUNTIME_MANIFEST_KEYS:
        (refs / f"{image_name}.ref").write_text(
            "ghcr.io/example/fb-agent:latest\n", encoding="utf-8"
        )

    with pytest.raises(PlanError, match="not immutable"):
        write_manifest(
            refs,
            "release-42",
            tmp_path / "release.json",
            "redis:7-alpine@sha256:" + "f" * 64,
        )


def test_workflow_builds_only_missing_content_tags_and_emits_current_release() -> None:
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/publish-images.yml"
    ).read_text(encoding="utf-8")

    assert "python3 scripts/ci_image_plan.py matrix --group app" in workflow
    assert "python3 scripts/ci_image_plan.py matrix --group desktop" in workflow
    assert "IMAGE_REPOSITORY: ghcr.io/${{ github.repository }}" in workflow
    assert "tags: ${{ steps.probe.outputs.candidate }}" in workflow
    assert workflow.count("steps.probe.outputs.exists != 'true'") >= 5
    assert "manifest unknown|no such manifest|not found" in workflow
    assert '--release-id "${{ inputs.release_id }}"' in workflow
    assert "--output release.json" in workflow
    assert 'd["schema"] == "fb-agent-release/v1"' in workflow
    assert "docker/Dockerfile.postgres" not in workflow
    assert "build-args: BASE_IMAGE=${{ steps.python-base.outputs.reference }}" in workflow
    assert "printf 'reference=%s@%s\\n'" in workflow
    release = (Path(__file__).resolve().parents[2] / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    assert "name: Deploy fbctl bundle" in release
    assert "scripts/fbctl publish" in release
    assert "--release-manifest release-inputs/release.json" in release


def test_workflow_actions_are_pinned_to_commits() -> None:
    workflow = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(
            (Path(__file__).resolve().parents[2] / ".github/workflows").glob("*.yml")
        )
    )
    refs = re.findall(r"^\s*uses:\s+[^@\s]+@([^\s#]+)", workflow, re.MULTILINE)
    assert refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in refs)
