from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _function(source: str, name: str, following: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index(f"\n}}\n\n{following}() {{", start) + 3
    return source[start:end]


@pytest.mark.parametrize(
    (
        "failpoint",
        "transaction_status",
        "active_state",
        "journal_candidate",
        "journal_previous",
        "expected",
    ),
    [
        (
            "after_prepare_before_pointer",
            "previous",
            "/state/desktop-states/previous",
            "candidate",
            "previous",
            "previous_pending",
        ),
        (
            "after_candidate_pointer_before_process_flag",
            "candidate",
            "/state/desktop-states/candidate",
            "candidate",
            "previous",
            "candidate_pending",
        ),
        (
            "after_candidate_reconcile",
            "candidate",
            "/state/desktop-states/candidate",
            "candidate",
            "previous",
            "candidate_pending",
        ),
        (
            "after_previous_pointer_before_runtime_convergence",
            "previous",
            "/state/desktop-states/previous",
            "candidate",
            "previous",
            "previous_pending",
        ),
        (
            "after_previous_runtime_and_journal_completion",
            "none",
            "/state/desktop-states/previous",
            "",
            "",
            "previous_final",
        ),
        (
            "after_candidate_journal_completion_before_disarm",
            "none",
            "/state/desktop-states/candidate",
            "",
            "",
            "candidate_final",
        ),
        (
            "split_pointer_and_journal",
            "previous",
            "/state/desktop-states/candidate",
            "candidate",
            "previous",
            "invalid",
        ),
        (
            "wrong_candidate_identity",
            "candidate",
            "/state/desktop-states/candidate",
            "other-candidate",
            "previous",
            "invalid",
        ),
        (
            "unreadable_or_unknown_status",
            "invalid-status",
            "/state/desktop-states/previous",
            "",
            "",
            "invalid",
        ),
    ],
)
def test_total_desktop_outcome_models_every_cutover_failpoint(
    tmp_path: Path,
    failpoint: str,
    transaction_status: str,
    active_state: str,
    journal_candidate: str,
    journal_previous: str,
    expected: str,
) -> None:
    parent = _source("scripts/server-platform-release.sh")
    classifier = _function(
        parent,
        "classify_desktop_release_outcome",
        "desktop_release_outcome",
    )
    harness = tmp_path / f"{failpoint}.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"{classifier}\n"
        'classify_desktop_release_outcome "$@"\n',
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            str(harness),
            transaction_status,
            active_state,
            "/state/desktop-states/candidate",
            "/state/desktop-states/previous",
            journal_candidate,
            journal_previous,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    ("transaction_status", "active_state", "expected"),
    [
        ("absent", "", "absent_pending"),
        ("none", "", "absent_final"),
        ("candidate", "/state/desktop-states/candidate", "candidate_pending"),
        ("none", "/state/desktop-states/candidate", "candidate_final"),
    ],
)
def test_total_desktop_outcome_handles_fresh_install_without_a_fake_previous(
    tmp_path: Path,
    transaction_status: str,
    active_state: str,
    expected: str,
) -> None:
    parent = _source("scripts/server-platform-release.sh")
    classifier = _function(
        parent,
        "classify_desktop_release_outcome",
        "desktop_release_outcome",
    )
    harness = tmp_path / "fresh.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"{classifier}\n"
        'classify_desktop_release_outcome "$1" "$2" '
        '"/state/desktop-states/candidate" "" "candidate" ""\n',
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        ["bash", str(harness), transaction_status, active_state],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("initial_pointer", ["previous", "candidate"])
def test_bounded_rollback_converges_even_when_process_commit_flag_is_stale(
    tmp_path: Path,
    initial_pointer: str,
) -> None:
    desktop = _source("scripts/platform-desktop-release.sh")
    rollback_start = desktop.index("rollback() {")
    rollback_end = desktop.index("\n}\nhandle_termination() {", rollback_start) + 3
    rollback = desktop[rollback_start:rollback_end]

    shared = tmp_path / "shared"
    states = shared / "desktop-states"
    previous = states / "previous"
    candidate = states / "candidate"
    previous.mkdir(parents=True)
    candidate.mkdir()
    active = shared / "active-desktop-state"
    active.symlink_to(f"desktop-states/{initial_pointer}")
    transaction = shared / "desktop-transaction.env"
    transaction.write_text(
        "schema=fb-agent-desktop-transaction-v1\n"
        "candidate_state=candidate\n"
        "previous_state=previous\n",
        encoding="utf-8",
    )
    events = tmp_path / "events.log"
    harness = tmp_path / f"rollback-{initial_pointer}.sh"

    q = shlex.quote
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"ACTIVE_DESKTOP_STATE={q(str(active))}\n"
        f"DESKTOP_TRANSACTION={q(str(transaction))}\n"
        f"PREVIOUS_STATE={q(str(previous))}\n"
        f"CANDIDATE_STATE={q(str(candidate))}\n"
        f"EVENTS={q(str(events))}\n"
        'PROJECT_DIR="/candidate/release"\n'
        'RELEASE_ENV="/candidate/release-images.env"\n'
        'APP_ENV="/candidate/app.env"\n'
        'PREVIOUS_RELEASE_DIR="/previous/release"\n'
        'PREVIOUS_MANIFEST="/previous/release-images.env"\n'
        'PREVIOUS_APP_ENV="/previous/app.env"\n'
        'PREVIOUS_VISION_ENV="/previous/vision.env"\n'
        'SCRIPT_DIR="/unused"\n'
        'previous_release_id="previous"\n'
        'DEADLINE_EPOCH="4102444800"\n'
        "ROLLBACK_IN_PROGRESS=false\n"
        "ROLLBACK_ARMED=true\n"
        "PRESERVE_MAINTENANCE_LEASE=false\n"
        "STATE_COMMITTED=false\n"
        "TRANSACTION_PREPARED=true\n"
        "log() { :; }\n"
        "require_phase_deadline() { return 0; }\n"
        "browser_maintenance_checkpoint() { return 0; }\n"
        "browser_maintenance_is_held() { return 0; }\n"
        'mark_desktop_rollback_failed() { printf \'mark:%s\\n\' "$1" >>"$EVENTS"; }\n'
        "remove_browser_container() { printf 'remove\\n' >>\"$EVENTS\"; }\n"
        "atomic_relative_symlink() {\n"
        '  ln -s "$1" "${2}.new"\n'
        "  python3 -c 'import os,sys; os.replace(sys.argv[1], sys.argv[2])' "
        '"${2}.new" "$2"\n'
        "  printf 'pointer-previous\\n' >>\"$EVENTS\"\n"
        "}\n"
        "run_before_deadline() {\n"
        '  local label="$1"\n'
        "  shift\n"
        '  case "$label" in\n'
        "    desktop_transaction_rollback_reconcile)\n"
        "      printf 'reconcile-previous\\n' >>\"$EVENTS\"\n"
        "      printf 'previous\\n'\n"
        "      ;;\n"
        "    desktop_transaction_rollback_complete)\n"
        "      printf 'complete-previous\\n' >>\"$EVENTS\"\n"
        "      python3 -c 'import os,sys; os.unlink(sys.argv[1])' \"$DESKTOP_TRANSACTION\"\n"
        "      ;;\n"
        "    previous_vision_restore)\n"
        "      printf 'vision-previous\\n' >>\"$EVENTS\"\n"
        "      ;;\n"
        '    *) printf \'run:%s\\n\' "$label" >>"$EVENTS" ;;\n'
        "  esac\n"
        "}\n"
        "timeout_cap() { printf '5\\n'; }\n"
        "run_browser_compose() { printf 'browser-previous\\n' >>\"$EVENTS\"; }\n"
        "desktop_auth_ready() { printf 'direct-auth-previous\\n' >>\"$EVENTS\"; }\n"
        "previous_vision_control_ready() { printf 'vision-control-previous\\n' >>\"$EVENTS\"; }\n"
        "browser_identity_is_exact() { printf 'identity-previous\\n' >>\"$EVENTS\"; }\n"
        "committed_desktop_auth_ready() { printf 'candidate-app-readyz\\n' >>\"$EVENTS\"; return 1; }\n"
        "ensure_cdp() { return 0; }\n"
        "sleep_before_deadline() { return 0; }\n"
        f"{rollback}\n"
        "rollback 1\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        ["bash", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1, result.stderr
    assert active.resolve() == previous.resolve()
    assert not transaction.exists()
    calls = events.read_text(encoding="utf-8").splitlines()
    assert "reconcile-previous" in calls
    assert "vision-previous" in calls
    assert "browser-previous" in calls
    assert "direct-auth-previous" in calls
    assert "vision-control-previous" in calls
    assert "identity-previous" in calls
    assert "complete-previous" in calls
    assert "candidate-app-readyz" not in calls
    if initial_pointer == "candidate":
        assert "pointer-previous" in calls


def test_signal_after_transaction_completion_preserves_durable_candidate(
    tmp_path: Path,
) -> None:
    desktop = _source("scripts/platform-desktop-release.sh")
    rollback_start = desktop.index("rollback() {")
    rollback_end = desktop.index("\n}\nhandle_termination() {", rollback_start) + 3
    rollback = desktop[rollback_start:rollback_end]

    shared = tmp_path / "shared"
    states = shared / "desktop-states"
    previous = states / "previous"
    candidate = states / "candidate"
    previous.mkdir(parents=True)
    candidate.mkdir()
    active = shared / "active-desktop-state"
    active.symlink_to("desktop-states/candidate")
    events = tmp_path / "events.log"
    transaction = shared / "desktop-transaction.env"
    harness = tmp_path / "after-complete.sh"
    q = shlex.quote
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"ACTIVE_DESKTOP_STATE={q(str(active))}\n"
        f"DESKTOP_TRANSACTION={q(str(transaction))}\n"
        f"PREVIOUS_STATE={q(str(previous))}\n"
        f"CANDIDATE_STATE={q(str(candidate))}\n"
        f"EVENTS={q(str(events))}\n"
        'DEADLINE_EPOCH="4102444800"\n'
        "ROLLBACK_IN_PROGRESS=false\n"
        "ROLLBACK_ARMED=true\n"
        "PRESERVE_MAINTENANCE_LEASE=false\n"
        "STATE_COMMITTED=false\n"
        "TRANSACTION_PREPARED=false\n"
        "log() { :; }\n"
        "require_phase_deadline() { return 0; }\n"
        "browser_maintenance_checkpoint() { return 0; }\n"
        "mark_desktop_rollback_failed() { :; }\n"
        "remove_browser_container() { printf 'remove\\n' >>\"$EVENTS\"; }\n"
        f"{rollback}\n"
        "rollback 1\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        ["bash", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 75, result.stderr
    assert active.resolve() == candidate.resolve()
    assert not transaction.exists()
    assert not events.exists()


def test_parent_outcome_verification_is_exact_and_fail_closed() -> None:
    parent = _source("scripts/server-platform-release.sh")
    outcome = _function(
        parent,
        "desktop_release_outcome",
        "reconcile_or_mark_critical",
    )
    rollback = parent[
        parent.index("rollback_parent_release() {") : parent.index("\n}\n\nhandle_termination() {")
    ]

    assert 'printf "invalid\\n"' not in outcome
    assert "printf 'invalid\\n'" in outcome
    assert 'readlink -f "$STATE_DIR/active-desktop-state"' in outcome
    assert '"$STATE_DIR/desktop-states"' in outcome
    assert 'release-state.py" desktop-verify' in outcome
    assert "EXPECTED_DESKTOP_CANDIDATE_STATE" in outcome
    assert "PREVIOUS_DESKTOP_STATE" in outcome
    assert "|| true" not in outcome

    assert "candidate_pending)" in rollback
    assert "desktop_candidate_pending_forward_reconciliation" in rollback
    assert "previous_pending|absent_pending)" in rollback
    assert "converge_pending_desktop_rollback" in rollback
    assert "desktop_previous_pending_rollback_nonconvergent" in rollback
    assert "invalid|*)" in rollback
    assert "desktop_release_outcome_invalid" in rollback
    assert rollback.index("converge_pending_desktop_rollback") < rollback.index(
        'release-state.py" rollback-commit'
    )


@pytest.mark.parametrize(
    ("first_release", "captured", "active", "transaction_status", "accepted"),
    [
        (False, "previous", "previous", "none", True),
        (False, "previous", "replacement", "none", False),
        (False, "previous", "previous", "candidate", False),
        (True, "", "", "none", True),
        (True, "", "replacement", "none", False),
    ],
)
def test_post_maintenance_snapshot_rejects_desktop_pointer_or_journal_race(
    tmp_path: Path,
    first_release: bool,
    captured: str,
    active: str,
    transaction_status: str,
    accepted: bool,
) -> None:
    parent = _source("scripts/server-platform-release.sh")
    check = _function(
        parent,
        "desktop_cutover_snapshot_is_current",
        "reconcile_or_mark_critical",
    )
    shared = tmp_path / "shared"
    states = shared / "desktop-states"
    states.mkdir(parents=True)
    for name in ("previous", "replacement"):
        (states / name).mkdir()
    active_pointer = shared / "active-desktop-state"
    if active:
        active_pointer.symlink_to(f"desktop-states/{active}")

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    status_file = tmp_path / "transaction-status"
    status_file.write_text(f"{transaction_status}\n", encoding="utf-8")
    transaction = scripts / "platform-desktop-transaction.sh"
    transaction.write_text(
        f"#!/usr/bin/env bash\ncat {shlex.quote(str(status_file))}\n",
        encoding="utf-8",
    )
    transaction.chmod(0o755)
    (scripts / "release-state.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "raise SystemExit(0 if sys.argv[1] == 'desktop-verify' else 2)\n",
        encoding="utf-8",
    )

    captured_path = "" if not captured else str(states / captured)
    harness = tmp_path / "snapshot-check.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"SCRIPT_DIR={shlex.quote(str(scripts))}\n"
        f"STATE_DIR={shlex.quote(str(shared))}\n"
        f"FIRST_RELEASE={'true' if first_release else 'false'}\n"
        f"PREVIOUS_DESKTOP_STATE={shlex.quote(captured_path)}\n"
        f"{check}\n"
        "desktop_cutover_snapshot_is_current\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        ["bash", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert (result.returncode == 0) is accepted, result.stderr


def test_second_signal_cannot_interrupt_child_stop_then_parent_rollback(
    tmp_path: Path,
) -> None:
    parent = _source("scripts/server-platform-release.sh")
    start = parent.index("handle_termination() {")
    end = (
        parent.index(
            "\n}\ntrap 'handle_termination 129' HUP",
            start,
        )
        + 3
    )
    handler = parent[start:end]
    events = tmp_path / "events.log"
    harness = tmp_path / "double-signal.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"EVENTS={shlex.quote(str(events))}\n"
        "ROLLBACK_ARMED=true\n"
        "ROLLBACK_IN_PROGRESS=false\n"
        "terminate_supervised_child() {\n"
        "  printf 'terminate-start\\n' >>\"$EVENTS\"\n"
        "  sleep 0.3\n"
        "  printf 'terminate-end\\n' >>\"$EVENTS\"\n"
        "}\n"
        "rollback_parent_release() {\n"
        '  printf \'rollback:%s\\n\' "$1" >>"$EVENTS"\n'
        "}\n"
        f"{handler}\n"
        "trap 'handle_termination 129' HUP\n"
        "trap 'handle_termination 143' TERM\n"
        "trap 'handle_termination 130' INT\n"
        "MAIN_PID=$$\n"
        "(\n"
        "  sleep 0.05\n"
        '  kill -HUP "$MAIN_PID"\n'
        "  for _ in $(seq 1 100); do\n"
        '    grep -qx terminate-start "$EVENTS" 2>/dev/null && break\n'
        "    sleep 0.01\n"
        "  done\n"
        '  kill -TERM "$MAIN_PID"\n'
        ") &\n"
        "while :; do sleep 1; done\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        ["bash", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 129, result.stderr
    assert events.read_text(encoding="utf-8").splitlines() == [
        "terminate-start",
        "terminate-end",
        "rollback:129",
    ]


def test_second_signal_cannot_interrupt_desktop_rollback_handler(
    tmp_path: Path,
) -> None:
    desktop = _source("scripts/platform-desktop-release.sh")
    start = desktop.index("handle_termination() {")
    end = (
        desktop.index(
            "\n}\ntrap 'handle_termination 129' HUP",
            start,
        )
        + 3
    )
    handler = desktop[start:end]
    events = tmp_path / "events.log"
    harness = tmp_path / "desktop-double-signal.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"EVENTS={shlex.quote(str(events))}\n"
        "ROLLBACK_ARMED=true\n"
        "ROLLBACK_IN_PROGRESS=false\n"
        "rollback() {\n"
        '  printf \'rollback-start:%s\\n\' "$1" >>"$EVENTS"\n'
        "  sleep 0.3\n"
        '  printf \'rollback-end:%s\\n\' "$1" >>"$EVENTS"\n'
        "}\n"
        f"{handler}\n"
        "trap 'handle_termination 129' HUP\n"
        "trap 'handle_termination 143' TERM\n"
        "trap 'handle_termination 130' INT\n"
        "MAIN_PID=$$\n"
        "(\n"
        "  sleep 0.05\n"
        '  kill -HUP "$MAIN_PID"\n'
        "  for _ in $(seq 1 100); do\n"
        "    grep -q '^rollback-start:' \"$EVENTS\" 2>/dev/null && break\n"
        "    sleep 0.01\n"
        "  done\n"
        '  kill -TERM "$MAIN_PID"\n'
        ") &\n"
        "while :; do sleep 1; done\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        ["bash", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 129, result.stderr
    assert events.read_text(encoding="utf-8").splitlines() == [
        "rollback-start:129",
        "rollback-end:129",
    ]


def test_same_immutable_first_release_resumes_from_committed_app_and_no_desktop(
    tmp_path: Path,
) -> None:
    parent = _source("scripts/server-platform-release.sh")
    gate = _function(
        parent,
        "initial_forward_resume_matches_current_release",
        "resume_exact_initial_forward_release",
    )
    resume = _function(
        parent,
        "resume_exact_initial_forward_release",
        "initial_forward_resume_is_complete",
    )
    complete = _function(
        parent,
        "initial_forward_resume_is_complete",
        "reconcile_or_mark_critical",
    )

    root = tmp_path / "root"
    shared = root / "shared"
    release = root / "releases" / "release-1"
    scripts = release / "scripts"
    shared.mkdir(parents=True)
    scripts.mkdir(parents=True)
    app_env = release / "production.env"
    release_env = release / "release-images.env"
    active_app_env = shared / "active-app-copy.env"
    active_release_env = shared / "active-release-copy.env"
    for candidate, active, content in (
        (app_env, active_app_env, "CONFIG=exact\n"),
        (release_env, active_release_env, "RELEASE_ID=release-1\n"),
    ):
        candidate.write_text(content, encoding="utf-8")
        active.write_text(content, encoding="utf-8")
    (shared / "release-transaction.json").write_text("{}\n", encoding="utf-8")
    (shared / "active-state").symlink_to("active-states/release-1")
    events = tmp_path / "events.log"

    (scripts / "platform-desktop-transaction.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'none\\n'\n",
        encoding="utf-8",
    )
    (scripts / "platform-desktop-transaction.sh").chmod(0o755)
    (scripts / "release-state.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "if sys.argv[1] == 'desktop-digest':\n"
        "    print('a' * 64)\n"
        "elif sys.argv[1] != 'desktop-verify':\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    reconciler = scripts / "reconcile-platform-release.sh"
    expected_desktop = shared / "desktop-states" / "release-1-aaaaaaaaaaaaaaaa"
    reconciler.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"printf '%s\\n' \"$*\" >{shlex.quote(str(events))}\n"
        '[[ " $* " == *" --resume-initial-forward "* ]]\n'
        '[[ " $* " != *" arm-cutover "* ]]\n'
        f"rm -f -- {shlex.quote(str(shared / 'release-transaction.json'))}\n"
        f"mkdir -p -- {shlex.quote(str(expected_desktop))}\n"
        f"ln -s desktop-states/release-1-aaaaaaaaaaaaaaaa "
        f"{shlex.quote(str(shared / 'active-desktop-state'))}\n",
        encoding="utf-8",
    )
    reconciler.chmod(0o755)

    harness = tmp_path / "initial-forward-retry.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"ROOT_DIR={shlex.quote(str(root))}\n"
        f"STATE_DIR={shlex.quote(str(shared))}\n"
        f"RELEASE_DIR={shlex.quote(str(release))}\n"
        f"SCRIPT_DIR={shlex.quote(str(scripts))}\n"
        f"APP_ENV={shlex.quote(str(app_env))}\n"
        f"RELEASE_ENV={shlex.quote(str(release_env))}\n"
        "release_id=release-1\n"
        "ROLLBACK_DEADLINE_SECONDS=180\n"
        "logger() { :; }\n"
        'timeout() { shift 4; "$@"; }\n'
        "state() {\n"
        '  case "$*" in\n'
        "    '--source journal --field recovery_policy') printf 'initial_forward_only\\n' ;;\n"
        "    '--source journal --field stage') printf 'committed\\n' ;;\n"
        "    '--source active --field state_dir') printf '/state/app-candidate\\n' ;;\n"
        "    '--source candidate --field state_dir') "
        "printf '%s\\n' \"${JOURNAL_CANDIDATE_STATE:-/state/app-candidate}\" ;;\n"
        "    '--source active --field color') printf 'blue\\n' ;;\n"
        "    '--source active --field release_id') printf 'release-1\\n' ;;\n"
        f"    '--source active --field release_dir') printf '%s\\n' "
        f"{shlex.quote(str(release))} ;;\n"
        f"    '--source active --field app_env') printf '%s\\n' "
        f"{shlex.quote(str(active_app_env))} ;;\n"
        f"    '--source active --field release_env') printf '%s\\n' "
        f"{shlex.quote(str(active_release_env))} ;;\n"
        "    '--source previous --field state_dir'|"
        "'--source journal --field rollback_requested_at') return 2 ;;\n"
        "    *) return 2 ;;\n"
        "  esac\n"
        "}\n"
        f"{gate}\n"
        f"{resume}\n"
        f"{complete}\n"
        "initial_forward_resume_matches_current_release\n"
        "resume_exact_initial_forward_release\n"
        "initial_forward_resume_is_complete\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)

    wrong_owner = subprocess.run(
        ["bash", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        env={**os.environ, "JOURNAL_CANDIDATE_STATE": "/state/other-candidate"},
    )
    assert wrong_owner.returncode != 0
    assert (shared / "release-transaction.json").exists()
    assert not (shared / "active-desktop-state").exists()
    assert not events.exists()

    result = subprocess.run(
        ["bash", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert not (shared / "release-transaction.json").exists()
    assert (shared / "active-desktop-state").resolve() == expected_desktop
    invocation = events.read_text(encoding="utf-8")
    assert "--resume-initial-forward" in invocation
    assert f"--expected-release-dir {release}" in invocation
    assert "arm-cutover" not in invocation


def test_initial_forward_retry_is_exact_and_bypasses_new_app_cutover() -> None:
    parent = _source("scripts/server-platform-release.sh")
    reconciler = _source("scripts/reconcile-platform-release.sh")

    resume_branch = parent.index("if initial_forward_resume_matches_current_release; then")
    app_prepare = parent.index('release-state.py" prepare', resume_branch)
    bluegreen = parent.index('"$SCRIPT_DIR/bluegreen-deploy.sh"', app_prepare)
    assert resume_branch < app_prepare < bluegreen
    branch = parent[resume_branch:app_prepare]
    assert "resume_exact_initial_forward_release" in branch
    assert "initial_forward_resume_is_complete" in branch
    assert "exit 0" in branch
    assert "bluegreen-deploy.sh" not in branch
    assert "arm-cutover" not in branch

    strict = reconciler[
        reconciler.index('if [[ "$INITIAL_FORWARD_RESUME" == true ]]') : reconciler.index(
            'if [[ "$BOOT_MODE" == true && "$journal_present" == false',
        )
    ]
    for contract in (
        "initial_forward_only",
        "pre-desktop committed stage",
        "exact active candidate",
        "refuses a previous application state",
        "release identity changed",
        "app configuration changed",
        "image manifest changed",
        "requires an absent desktop pointer",
        "requires no desktop transaction",
    ):
        assert contract in strict
    forward_policy = reconciler[
        reconciler.index('if [[ "$recovery_policy" == initial_forward_only ]]') : reconciler.index(
            'elif [[ -z "$recovery_policy"',
            reconciler.index(
                'if [[ "$recovery_policy" == initial_forward_only ]]',
            ),
        )
    ]
    assert 'stage_is_before "$journal_stage" committed' in forward_policy
    assert forward_policy.index('stage_is_before "$journal_stage" committed') < (
        forward_policy.index("select_forward_candidate")
    )
    assert "committed_initial_forward_identity:changed" in forward_policy


def test_initial_forward_expired_deadline_records_original_without_rearming(
    tmp_path: Path,
) -> None:
    reconciler = _source("scripts/reconcile-platform-release.sh")
    recovery = _function(
        reconciler,
        "start_deadline_breach_recovery",
        "stage_rank",
    )
    events = tmp_path / "events.log"
    harness = tmp_path / "deadline-recovery.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"EVENTS={shlex.quote(str(events))}\n"
        'SCRIPT_DIR="/release/scripts"\n'
        'STATE_DIR="/root/shared"\n'
        "DEADLINE_SECONDS=180\n"
        "DEADLINE_EPOCH=''\n"
        "ORIGINAL_CUTOVER_DEADLINE=''\n"
        "BOOT_MODE=false\n"
        "BOOT_RECOVERY_DEADLINE=false\n"
        "INITIAL_FORWARD_RESUME=true\n"
        "INITIAL_FORWARD_RECOVERY_DEADLINE=false\n"
        'python3() { printf \'python:%s\\n\' "$*" >>"$EVENTS"; }\n'
        'logger() { printf \'logger:%s\\n\' "$*" >>"$EVENTS"; }\n'
        'log() { printf \'log:%s\\n\' "$*" >>"$EVENTS"; }\n'
        f"{recovery}\n"
        "start_deadline_breach_recovery 1000 900\n"
        "printf 'state:%s:%s:%s\\n' "
        '"$ORIGINAL_CUTOVER_DEADLINE" "$DEADLINE_EPOCH" '
        '"$INITIAL_FORWARD_RECOVERY_DEADLINE" >>"$EVENTS"\n',
        encoding="utf-8",
    )
    harness.chmod(0o755)

    result = subprocess.run(
        ["bash", str(harness)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    calls = events.read_text(encoding="utf-8").splitlines()
    assert any(
        "mark-rollback-failed --state-root /root/shared "
        "--failure cutover_deadline_breached:original_900" in call
        for call in calls
    )
    assert "state:900:1180:true" in calls
    assert all("arm-cutover" not in call for call in calls)


def test_desktop_journal_precedes_every_forward_runtime_mutation() -> None:
    desktop = _source("scripts/platform-desktop-release.sh")
    main = desktop[desktop.index('if [[ "$ROLLBACK_ONLY" == true ]]') :]

    state_prepare = main.index('prepare_desktop_state "$candidate_fingerprint"')
    transaction_prepare = main.index(
        '"$SCRIPT_DIR/platform-desktop-transaction.sh" prepare',
        state_prepare,
    )
    browser_remove = main.index(
        'remove_browser_container "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV"',
        transaction_prepare,
    )
    vision_mutation = main.index("--defer-commit", browser_remove)
    unit_mutation = main.index('install_desktop_units "$PROJECT_DIR"', vision_mutation)
    pointer_commit = main.index('"$ACTIVE_DESKTOP_STATE"', unit_mutation)

    assert (
        state_prepare
        < transaction_prepare
        < browser_remove
        < vision_mutation
        < unit_mutation
        < pointer_commit
    )
    assert main.count('"$SCRIPT_DIR/platform-desktop-transaction.sh" prepare') == 1


def test_pending_rollback_is_bounded_signal_resistant_and_directly_verified() -> None:
    parent = _source("scripts/server-platform-release.sh")
    desktop = _source("scripts/platform-desktop-release.sh")
    parent_rollback = parent[
        parent.index("rollback_parent_release() {") : parent.index("\n}\n\nhandle_termination() {")
    ]
    desktop_rollback = desktop[
        desktop.index("rollback() {") : desktop.index("\n}\nhandle_termination() {")
    ]
    rollback_only = desktop[
        desktop.index('if [[ "$ROLLBACK_ONLY" == true ]]') : desktop.index(
            'if [[ -n "$PREVIOUS_STATE" \\',
            desktop.index('if [[ "$ROLLBACK_ONLY" == true ]]'),
        )
    ]

    assert "trap ':' HUP TERM INT" in parent_rollback
    assert "trap ':' HUP TERM INT" in desktop_rollback
    parent_handler = parent[
        parent.index("handle_termination() {") : parent.index(
            "\n}\ntrap 'handle_termination 129' HUP"
        )
    ]
    desktop_handler = desktop[
        desktop.index("handle_termination() {") : desktop.index(
            "\n}\ntrap 'handle_termination 129' HUP"
        )
    ]
    assert "trap ':' HUP TERM INT" in parent_handler
    assert "trap ':' HUP TERM INT" in desktop_handler
    assert "--rollback-only" in parent
    assert "previous|absent)" in rollback_only
    assert "rollback 0" in rollback_only
    assert "desktop_rollback_only_refused_" in rollback_only

    direct_recheck = desktop_rollback[
        desktop_rollback.index("for attempt in $(seq 1 60)") : desktop_rollback.index(
            "if ! browser_maintenance_is_held"
        )
    ]
    assert 'desktop_auth_ready "$PREVIOUS_VISION_ENV"' in direct_recheck
    assert "previous_vision_control_ready" in direct_recheck
    assert "browser_identity_is_exact" in direct_recheck
    assert "committed_desktop_auth_ready" not in direct_recheck


def test_unchanged_desktop_contract_cannot_bypass_exact_candidate_acceptance() -> None:
    parent = _source("scripts/server-platform-release.sh")
    desktop = _source("scripts/platform-desktop-release.sh")
    exact_retry = desktop[
        desktop.index("# A retry of the exact already-committed immutable desktop") : desktop.index(
            "# Persist both rollback identities",
            desktop.index("# A retry of the exact already-committed immutable desktop"),
        )
    ]

    assert 'candidate_fingerprint" == "$previous_fingerprint' not in desktop
    assert '"$CANDIDATE_STATE" == "$PREVIOUS_STATE"' in exact_retry
    assert "wait_for_candidate_desktop_readiness" in exact_retry
    assert "wait_for_committed_desktop_readiness" in exact_retry
    assert "browser_identity_is_exact" in exact_retry
    assert "systemctl is-active --quiet fb-agent-desktop-agent.service" in exact_retry
    assert "activate_desktop_units" not in exact_retry

    successful_child = parent.index(
        '"$SCRIPT_DIR/platform-desktop-release.sh"',
        parent.index('"$SCRIPT_DIR/bluegreen-deploy.sh"'),
    )
    final_acceptance = parent.index(
        '"$(desktop_release_outcome)" == candidate_final',
        successful_child,
    )
    fence_release = parent.index("browser_maintenance_leave", final_acceptance)
    assert successful_child < final_acceptance < fence_release
