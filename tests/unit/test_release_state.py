from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/release-state.py"


def _run(*args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["python3", str(SCRIPT), *(str(value) for value in args)],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


def _file(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    app_root = tmp_path / "fb-agent"
    state_root = app_root / "shared"
    release_root = app_root / "releases"
    state_root.mkdir(parents=True, mode=0o700)
    release_root.mkdir(mode=0o700)
    return app_root, state_root, release_root


def _seal_release(release_dir: Path) -> None:
    for directory, directory_names, file_names in os.walk(
        release_dir,
        topdown=False,
    ):
        root = Path(directory)
        for name in file_names:
            path = root / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
        for name in directory_names:
            path = root / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
    release_dir.chmod(release_dir.stat().st_mode & ~0o222)


def _complete(state_root: Path) -> None:
    _run(
        "stage",
        "--state-root",
        state_root,
        "--stage",
        "desktop_adopted",
    )
    _run("complete", "--state-root", state_root)


def _prepare(
    state_root: Path,
    release_root: Path,
    *,
    release_id: str,
    color: str,
    app_value: str,
) -> Path:
    release_dir = release_root / release_id
    release_dir.mkdir(mode=0o700)
    _file(release_dir / "payload.txt", f"{release_id}:{app_value}\n")
    _run(
        "manifest-write",
        "--release-dir",
        release_dir,
        "--manifest",
        release_dir / ".fb-agent-source-manifest.json",
    )
    _seal_release(release_dir)
    app_env = _file(state_root / f"{release_id}.app.env", f"VALUE={app_value}\n")
    manifest = _file(state_root / f"{release_id}.release.env", f"RELEASE_ID={release_id}\n")
    result = _run(
        "prepare",
        "--state-root",
        state_root,
        "--release-root",
        release_root,
        "--release-dir",
        release_dir,
        "--app-env",
        app_env,
        "--release-env",
        manifest,
        "--release-id",
        release_id,
        "--color",
        color,
    )
    return Path(result.stdout.strip())


def _accept_and_commit(state_root: Path, candidate: Path, *, failpoint: str | None = None):
    _run("begin", "--state-root", state_root, "--candidate-state", candidate)
    _run(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        int(time.time()) + 120,
    )
    _run("stage", "--state-root", state_root, "--stage", "accepted")
    arguments: list[object] = ["commit", "--state-root", state_root]
    if failpoint:
        arguments.extend(("--failpoint", failpoint))
    return _run(*arguments, check=failpoint is None)


def _active_path(state_root: Path) -> Path:
    result = _run("get", "--state-root", state_root, "--source", "active", "--field", "state_dir")
    return Path(result.stdout.strip())


def test_crash_before_atomic_pointer_keeps_previous_committed_state(tmp_path: Path) -> None:
    app_root, state_root, release_root = _layout(tmp_path)
    previous = _prepare(
        state_root, release_root, release_id="release-1", color="blue", app_value="old"
    )
    _accept_and_commit(state_root, previous)
    _run("ensure-links", "--state-root", state_root, "--root-dir", app_root)
    _complete(state_root)

    candidate = _prepare(
        state_root, release_root, release_id="release-2", color="green", app_value="new"
    )
    crashed = _accept_and_commit(state_root, candidate, failpoint="before-pointer")

    assert crashed.returncode == 86
    assert _active_path(state_root) == previous
    assert (state_root / "release-transaction.json").is_file()
    assert (app_root / "current").resolve() == release_root / "release-1"


def test_crash_after_atomic_pointer_recovers_new_state_idempotently(tmp_path: Path) -> None:
    app_root, state_root, release_root = _layout(tmp_path)
    previous = _prepare(
        state_root, release_root, release_id="release-1", color="blue", app_value="old"
    )
    _accept_and_commit(state_root, previous)
    _run("ensure-links", "--state-root", state_root, "--root-dir", app_root)
    _complete(state_root)
    candidate = _prepare(
        state_root, release_root, release_id="release-2", color="green", app_value="new"
    )

    crashed = _accept_and_commit(state_root, candidate, failpoint="after-pointer")

    assert crashed.returncode == 87
    assert _active_path(state_root) == candidate
    assert (app_root / "current").resolve() == release_root / "release-2"
    # Reconciliation can safely repeat commit after seeing that the pointer is
    # already on the journal candidate, then archive the durable journal.
    _run("commit", "--state-root", state_root)
    _complete(state_root)
    assert not (state_root / "release-transaction.json").exists()
    history = list((state_root / "release-transactions").glob("*.json"))
    assert len(history) == 2
    assert any(json.loads(path.read_text())["stage"] == "completed" for path in history)


def test_committed_candidate_can_atomically_reselect_previous_after_desktop_failure(
    tmp_path: Path,
) -> None:
    _, state_root, release_root = _layout(tmp_path)
    previous = _prepare(
        state_root,
        release_root,
        release_id="release-1",
        color="blue",
        app_value="old",
    )
    _accept_and_commit(state_root, previous)
    _complete(state_root)
    candidate = _prepare(
        state_root,
        release_root,
        release_id="release-2",
        color="green",
        app_value="new",
    )
    _accept_and_commit(state_root, candidate)

    crashed = _run(
        "rollback-commit",
        "--state-root",
        state_root,
        "--failpoint",
        "after-pointer",
        check=False,
    )
    assert crashed.returncode == 91
    assert _active_path(state_root) == previous
    interrupted = json.loads((state_root / "release-transaction.json").read_text(encoding="utf-8"))
    assert interrupted["stage"] == "committed"
    assert interrupted["rollback_requested_at"]
    assert interrupted["rollback_from_release_id"] == "release-2"

    _run("rollback-commit", "--state-root", state_root)
    _run("rollback-commit", "--state-root", state_root)
    journal = json.loads((state_root / "release-transaction.json").read_text(encoding="utf-8"))
    assert journal["stage"] == "accepted"
    assert journal["rollback_from_release_id"] == "release-2"

    _run("abort", "--state-root", state_root)
    assert not (state_root / "release-transaction.json").exists()
    history = (state_root / "release-transactions").glob("*.json")
    assert any(
        json.loads(path.read_text(encoding="utf-8")).get("outcome") == "rolled_back"
        for path in history
    )


def test_committed_rollback_intent_survives_crash_before_pointer(
    tmp_path: Path,
) -> None:
    _, state_root, release_root = _layout(tmp_path)
    previous = _prepare(
        state_root,
        release_root,
        release_id="release-1",
        color="blue",
        app_value="old",
    )
    _accept_and_commit(state_root, previous)
    _complete(state_root)
    candidate = _prepare(
        state_root,
        release_root,
        release_id="release-2",
        color="green",
        app_value="new",
    )
    _accept_and_commit(state_root, candidate)

    crashed = _run(
        "rollback-commit",
        "--state-root",
        state_root,
        "--failpoint",
        "before-pointer",
        check=False,
    )

    assert crashed.returncode == 90
    assert _active_path(state_root) == candidate
    interrupted = json.loads((state_root / "release-transaction.json").read_text(encoding="utf-8"))
    assert interrupted["stage"] == "committed"
    assert interrupted["rollback_requested_at"]

    _run("rollback-commit", "--state-root", state_root)
    assert _active_path(state_root) == previous
    recovered = json.loads((state_root / "release-transaction.json").read_text(encoding="utf-8"))
    assert recovered["stage"] == "accepted"
    assert recovered["rollback_selected_at"]


def test_state_prepare_rejects_symlinked_release_and_parent_traversal(tmp_path: Path) -> None:
    _, state_root, release_root = _layout(tmp_path)
    real_release = release_root / "release-1"
    real_release.mkdir()
    _run(
        "manifest-write",
        "--release-dir",
        real_release,
        "--manifest",
        real_release / ".fb-agent-source-manifest.json",
    )
    _seal_release(real_release)
    linked_release = release_root / "linked"
    os.symlink(real_release, linked_release)
    app_env = _file(state_root / "app.env", "VALUE=1\n")
    manifest = _file(state_root / "release.env", "RELEASE_ID=release-1\n")

    symlinked = _run(
        "prepare",
        "--state-root",
        state_root,
        "--release-root",
        release_root,
        "--release-dir",
        linked_release,
        "--app-env",
        app_env,
        "--release-env",
        manifest,
        "--release-id",
        "release-1",
        "--color",
        "blue",
        check=False,
    )
    traversed = _run(
        "prepare",
        "--state-root",
        state_root / ".." / "shared",
        "--release-root",
        release_root,
        "--release-dir",
        real_release,
        "--app-env",
        app_env,
        "--release-env",
        manifest,
        "--release-id",
        "release-1",
        "--color",
        "blue",
        check=False,
    )

    assert symlinked.returncode != 0
    assert "canonical" in symlinked.stderr or "symlink" in symlinked.stderr
    assert traversed.returncode != 0
    assert "without '..'" in traversed.stderr


def test_release_state_has_no_operator_environment_write_command(
    tmp_path: Path,
) -> None:
    _app_root, state_root, _release_root = _layout(tmp_path)
    desired = _file(state_root / ".env", "VALUE=operator-owned\n")
    before = desired.read_bytes()

    result = _run(
        "sync-desired-env",
        "--state-root",
        state_root,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid choice" in result.stderr
    assert desired.read_bytes() == before


def test_release_state_rejects_source_tree_mutation_after_manifest(tmp_path: Path) -> None:
    _, state_root, release_root = _layout(tmp_path)
    candidate = _prepare(
        state_root, release_root, release_id="release-1", color="blue", app_value="safe"
    )
    _accept_and_commit(state_root, candidate)
    payload = release_root / "release-1" / "payload.txt"
    payload.chmod(0o600)
    payload.write_text("mutated\n", encoding="utf-8")

    result = _run(
        "manifest-verify",
        "--release-dir",
        release_root / "release-1",
        "--manifest",
        release_root / "release-1" / ".fb-agent-source-manifest.json",
        check=False,
    )

    assert result.returncode != 0
    assert "differs from its immutable source manifest" in result.stderr


def test_release_manifest_and_upload_exclude_generated_frontend_artifacts(
    tmp_path: Path,
) -> None:
    release_dir = tmp_path / "release"
    generated_paths = (
        "frontend/storybook-static/index.html",
        "frontend/test-results/report.json",
        "frontend/.tanstack/router-manifest.json",
        "frontend/.experimental-vitest-cache/results.json",
        "frontend/playwright-report/index.html",
        "frontend/tsconfig.app.tsbuildinfo",
    )
    for relative in generated_paths:
        path = release_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated\n", encoding="utf-8")
    source = release_dir / "frontend/src/index.ts"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("export {};\n", encoding="utf-8")
    manifest_path = release_dir / ".fb-agent-source-manifest.json"

    _run(
        "manifest-write",
        "--release-dir",
        release_dir,
        "--manifest",
        manifest_path,
    )

    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = {entry["path"] for entry in document["entries"]}
    assert "frontend/src/index.ts" in entries
    for relative in generated_paths:
        assert relative not in entries
    assert not any(
        excluded in entry
        for entry in entries
        for excluded in (
            "/storybook-static",
            "/test-results",
            "/.tanstack",
            "/.experimental-vitest-cache",
            "/playwright-report",
        )
    )

    deploy = (ROOT / "scripts/deploy-platform-server.sh").read_text(encoding="utf-8")
    for pattern in (
        ".experimental-vitest-cache/",
        ".tanstack/",
        "playwright-report/",
        "storybook-static/",
        "test-results/",
        "*.tsbuildinfo",
    ):
        assert f"--exclude '{pattern}'" in deploy


def test_prepare_rejects_writable_release_and_complete_requires_adoption(
    tmp_path: Path,
) -> None:
    app_root, state_root, release_root = _layout(tmp_path)
    release_dir = release_root / "release-1"
    release_dir.mkdir(mode=0o700)
    _file(release_dir / "payload.txt", "safe\n")
    _run(
        "manifest-write",
        "--release-dir",
        release_dir,
        "--manifest",
        release_dir / ".fb-agent-source-manifest.json",
    )
    app_env = _file(state_root / "app.env", "VALUE=safe\n")
    release_env = _file(
        state_root / "release.env",
        "RELEASE_ID=release-1\n",
    )

    writable = _run(
        "prepare",
        "--state-root",
        state_root,
        "--release-root",
        release_root,
        "--release-dir",
        release_dir,
        "--app-env",
        app_env,
        "--release-env",
        release_env,
        "--release-id",
        "release-1",
        "--color",
        "blue",
        check=False,
    )
    assert writable.returncode != 0
    assert "release tree is writable" in writable.stderr

    _seal_release(release_dir)
    candidate = Path(
        _run(
            "prepare",
            "--state-root",
            state_root,
            "--release-root",
            release_root,
            "--release-dir",
            release_dir,
            "--app-env",
            app_env,
            "--release-env",
            release_env,
            "--release-id",
            "release-1",
            "--color",
            "blue",
        ).stdout.strip()
    )
    _accept_and_commit(state_root, candidate)
    _run("ensure-links", "--state-root", state_root, "--root-dir", app_root)

    premature = _run(
        "complete",
        "--state-root",
        state_root,
        check=False,
    )
    assert premature.returncode != 0
    assert "adoption must finish" in premature.stderr
    assert (state_root / "release-transaction.json").is_file()

    _complete(state_root)
    assert not (state_root / "release-transaction.json").exists()


def test_cutover_deadline_is_durable_idempotent_and_immutable(tmp_path: Path) -> None:
    _, state_root, release_root = _layout(tmp_path)
    candidate = _prepare(
        state_root, release_root, release_id="release-1", color="blue", app_value="safe"
    )
    _run("begin", "--state-root", state_root, "--candidate-state", candidate)
    deadline = int(time.time()) + 120

    _run(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        deadline,
    )
    _run(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        deadline,
    )
    moved = _run(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        deadline + 1,
        check=False,
    )

    assert moved.returncode != 0
    assert "immutable once armed" in moved.stderr
    stored = _run(
        "get",
        "--state-root",
        state_root,
        "--source",
        "journal",
        "--field",
        "cutover_deadline_epoch",
    )
    assert int(stored.stdout) == deadline


def test_commit_keeps_the_single_cutover_deadline_without_a_second_clock(
    tmp_path: Path,
) -> None:
    _, state_root, release_root = _layout(tmp_path)
    candidate = _prepare(
        state_root,
        release_root,
        release_id="release-1",
        color="blue",
        app_value="safe",
    )
    _run("begin", "--state-root", state_root, "--candidate-state", candidate)
    deadline = int(time.time()) + 120
    _run(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        deadline,
    )
    _run("stage", "--state-root", state_root, "--stage", "accepted")
    _run("commit", "--state-root", state_root)

    journal = json.loads((state_root / "release-transaction.json").read_text(encoding="utf-8"))
    assert journal["cutover_deadline_epoch"] == deadline
    assert "adoption_deadline_epoch" not in journal


def test_initial_blue_selection_persists_policy_before_atomic_pointer(tmp_path: Path) -> None:
    _, state_root, release_root = _layout(tmp_path)
    candidate = _prepare(
        state_root,
        release_root,
        release_id="release-1",
        color="blue",
        app_value="new",
    )
    _run("begin", "--state-root", state_root, "--candidate-state", candidate)
    _run("stage", "--state-root", state_root, "--stage", "candidate_started")
    _run(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        int(time.time()) + 120,
    )

    crashed = _run(
        "select-initial",
        "--state-root",
        state_root,
        "--failpoint",
        "after-policy",
        check=False,
    )

    assert crashed.returncode == 88
    assert not (state_root / "active-state").exists()
    policy = _run(
        "get",
        "--state-root",
        state_root,
        "--source",
        "journal",
        "--field",
        "recovery_policy",
    )
    assert policy.stdout.strip() == "initial_forward_only"

    _run("select-initial", "--state-root", state_root)
    _run("select-initial", "--state-root", state_root)
    assert _active_path(state_root) == candidate


def test_initial_selection_rejects_green_candidate(tmp_path: Path) -> None:
    _, state_root, release_root = _layout(tmp_path)
    candidate = _prepare(
        state_root, release_root, release_id="release-1", color="green", app_value="safe"
    )
    _run("begin", "--state-root", state_root, "--candidate-state", candidate)
    _run("stage", "--state-root", state_root, "--stage", "candidate_started")
    _run(
        "arm-cutover",
        "--state-root",
        state_root,
        "--deadline-epoch",
        int(time.time()) + 120,
    )

    result = _run("select-initial", "--state-root", state_root, check=False)

    assert result.returncode != 0
    assert "blue candidate" in result.stderr
