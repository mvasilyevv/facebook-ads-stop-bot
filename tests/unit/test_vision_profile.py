from __future__ import annotations

import base64
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from fbctl import __main__ as fbctl_main
from fbctl import controller as fbctl_controller
from fbctl import vision_profile as vision_profile
from fbctl.controller import bootstrap_host
from fbctl.errors import FbctlError
from fbctl.vision_profile import (
    VISION_PROFILE_MARKER,
    VISION_PROFILE_MARKER_CONTENT,
    bootstrap_profile_is_current,
    copy_profile_from_receipt,
    remove_profile_tree_receipt,
    validate_bootstrap_vision_profile,
)


def _write_profile(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700)
    path.chmod(0o700)
    marker = path / VISION_PROFILE_MARKER
    marker.write_bytes(VISION_PROFILE_MARKER_CONTENT)
    marker.chmod(0o600)
    browser = path / "browser"
    browser.mkdir(mode=0o700)
    browser.chmod(0o700)
    preferences = browser / "Preferences"
    preferences.write_text("{}", encoding="utf-8")
    preferences.chmod(0o600)
    return path


def _source(path: Path) -> Path:
    path.write_text(
        "".join(
            (
                f"ENCRYPTION_KEY={base64.urlsafe_b64encode(b'e' * 32).decode()}\n",
                "ENCRYPTION_KEY_VERIFY=verify\n",
                "TELEGRAM_BOT_TOKEN=123456:test\n",
                "TELEGRAM_OIDC_CLIENT_ID=123456\n",
                f"TELEGRAM_OIDC_CLIENT_SECRET={'s' * 40}\n",
                "API_KEY=" + "k" * 32 + "\n",
                "DESKTOP_OWNER_TELEGRAM_USER_ID=123456\n",
                "VISION_X_TOKEN=token\n",
                "VISION_PROFILE_ID=profile\n",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_bootstrap_profile_prefers_a_valid_managed_canonical_retry_state(tmp_path: Path) -> None:
    canonical = _write_profile(tmp_path / "shared" / "vision-config")
    result = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=None,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )

    assert result.canonical_profile == canonical
    assert result.seed_to_copy is None
    assert bootstrap_profile_is_current(result) is True

    _write_profile(canonical.parent / "desktop-profile-seed")

    assert bootstrap_profile_is_current(result) is False


def test_bootstrap_profile_accepts_a_valid_explicit_seed_when_canonical_is_absent(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "shared" / "vision-config"
    seed = _write_profile(tmp_path / "shared" / "desktop-profile-seed")

    result = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )

    assert result.seed_to_copy == seed
    assert not canonical.exists()


@pytest.mark.parametrize("unsafe", ["missing-marker", "writable-file", "hard-link", "symlink"])
def test_bootstrap_profile_rejects_unsafe_seed(tmp_path: Path, unsafe: str) -> None:
    canonical = tmp_path / "shared" / "vision-config"
    seed = _write_profile(tmp_path / "shared" / "desktop-profile-seed")
    preferences = seed / "browser" / "Preferences"
    if unsafe == "missing-marker":
        (seed / VISION_PROFILE_MARKER).unlink()
    elif unsafe == "writable-file":
        preferences.chmod(0o620)
    elif unsafe == "hard-link":
        os.link(preferences, seed / "browser" / "Preferences-copy")
    else:
        preferences.unlink()
        preferences.symlink_to("/etc/passwd")

    with pytest.raises(FbctlError, match="(marker is invalid|contains an unsafe entry)"):
        validate_bootstrap_vision_profile(
            canonical_profile=canonical,
            desktop_profile_seed=seed,
            seed_required_uid=os.getuid(),
            seed_required_gid=os.getgid(),
            canonical_required_uid=os.getuid(),
            canonical_required_gid=os.getgid(),
        )


@pytest.mark.parametrize("unsafe", ["missing-marker", "writable-file"])
def test_bootstrap_profile_rejects_unsafe_canonical_without_falling_back_to_seed(
    tmp_path: Path,
    unsafe: str,
) -> None:
    canonical = _write_profile(tmp_path / "shared" / "vision-config")
    seed = _write_profile(tmp_path / "shared" / "desktop-profile-seed")
    if unsafe == "missing-marker":
        (canonical / VISION_PROFILE_MARKER).unlink()
    else:
        (canonical / "browser" / "Preferences").chmod(0o620)

    with pytest.raises(FbctlError, match="(marker is invalid|contains an unsafe entry)"):
        validate_bootstrap_vision_profile(
            canonical_profile=canonical,
            desktop_profile_seed=seed,
            seed_required_uid=os.getuid(),
            seed_required_gid=os.getgid(),
            canonical_required_uid=os.getuid(),
            canonical_required_gid=os.getgid(),
        )


def test_invalid_seed_fails_before_bootstrap_writes_or_runner_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingRunner:
        def __init__(self) -> None:
            self.calls: list[object] = []

        def run(self, *args, **kwargs) -> None:
            self.calls.append((args, kwargs))
            raise AssertionError("runner must not be called")

    root = tmp_path / "fb-agent"
    shared = root / "shared"
    shared.mkdir(parents=True, mode=0o700)
    shared.chmod(0o700)
    seed = shared / "desktop-profile-seed"
    seed.mkdir(mode=0o700)
    seed.chmod(0o700)
    source = _source(tmp_path / "source.env")
    runner = RecordingRunner()
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    with pytest.raises(FbctlError, match="marker is invalid"):
        bootstrap_host(
            runner=runner,
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=seed,
            docker_config=None,
            rehearsal=True,
        )

    assert runner.calls == []
    assert not (shared / "source.env").exists()
    assert not (root / "candidate").exists()


def test_production_bootstrap_rejects_the_legacy_seed_path_before_host_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    with pytest.raises(FbctlError, match="approved production desktop profile seed"):
        bootstrap_host(
            runner=None,
            root=vision_profile.PRODUCTION_ROOT,
            source_env=None,
            source_env_payload=_source(tmp_path / "source.env").read_bytes(),
            adoption_bundle=None,
            desktop_profile_seed=(
                vision_profile.PRODUCTION_ROOT / "shared" / "vision-profile-seed"
            ),
            docker_config=None,
            rehearsal=True,
        )


@pytest.mark.parametrize(("field", "delta"), [("st_uid", 1), ("st_gid", 1)])
def test_profile_rejects_descendant_with_mixed_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    delta: int,
) -> None:
    canonical = tmp_path / "shared" / "vision-config"
    seed = _write_profile(tmp_path / "shared" / "desktop-profile-seed")
    target_inode = (seed / "browser" / "Preferences").stat().st_ino
    original_fstat = vision_profile.os.fstat

    def mismatched_fstat(descriptor: int):
        metadata = original_fstat(descriptor)
        if metadata.st_ino != target_inode:
            return metadata
        values = {
            name: getattr(metadata, name)
            for name in (
                "st_mode",
                "st_ino",
                "st_dev",
                "st_nlink",
                "st_uid",
                "st_gid",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        values[field] += delta
        return SimpleNamespace(**values)

    monkeypatch.setattr(vision_profile.os, "fstat", mismatched_fstat)

    with pytest.raises(FbctlError, match="invalid ownership"):
        validate_bootstrap_vision_profile(
            canonical_profile=canonical,
            desktop_profile_seed=seed,
            seed_required_uid=os.getuid(),
            seed_required_gid=os.getgid(),
            canonical_required_uid=os.getuid(),
            canonical_required_gid=os.getgid(),
        )


def test_profile_receipt_detects_marker_change_before_locked_write(tmp_path: Path) -> None:
    canonical = tmp_path / "shared" / "vision-config"
    seed = _write_profile(tmp_path / "shared" / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )

    (seed / VISION_PROFILE_MARKER).write_bytes(b"changed-vision-profile!!\n")

    assert bootstrap_profile_is_current(profile) is False


@pytest.mark.parametrize(
    "mutation",
    ["canonical-appears", "seed-appears", "seed-disappears", "seed-changes"],
)
def test_locked_recheck_binds_presence_and_absence_of_both_profile_paths(
    tmp_path: Path,
    mutation: str,
) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = shared / "desktop-profile-seed"
    if mutation == "seed-appears":
        _write_profile(canonical)
    else:
        _write_profile(seed)
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )

    if mutation == "canonical-appears":
        _write_profile(canonical)
    elif mutation == "seed-appears":
        _write_profile(seed)
    elif mutation == "seed-disappears":
        os.rename(seed, shared / "seed-moved")
    else:
        marker = seed / VISION_PROFILE_MARKER
        changed = bytearray(VISION_PROFILE_MARKER_CONTENT)
        changed[-2] = ord("2")
        marker.write_bytes(changed)

    assert bootstrap_profile_is_current(profile) is False


@pytest.mark.parametrize("race", ["canonical-appears", "seed-disappears"])
def test_profile_path_race_under_lock_fails_before_source_candidate_or_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    class RecordingRunner:
        def __init__(self) -> None:
            self.calls: list[object] = []

        def run(self, *args, **kwargs) -> None:
            self.calls.append((args, kwargs))
            raise AssertionError("runner must not be called")

    root = tmp_path / "fb-agent"
    shared = root / "shared"
    shared.mkdir(parents=True, mode=0o700)
    shared.chmod(0o700)
    seed = _write_profile(shared / "desktop-profile-seed")
    runner = RecordingRunner()
    original_validate = fbctl_controller.validate_bootstrap_vision_profile

    def validate_then_change_path_state(**kwargs):
        result = original_validate(**kwargs)
        if race == "canonical-appears":
            _write_profile(shared / "vision-config")
        else:
            os.rename(seed, shared / "seed-moved")
        return result

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(fbctl_controller, "VISION_RUNTIME_UID", os.getuid())
    monkeypatch.setattr(fbctl_controller, "VISION_RUNTIME_GID", os.getgid())
    monkeypatch.setattr(
        fbctl_controller,
        "validate_bootstrap_vision_profile",
        validate_then_change_path_state,
    )

    with pytest.raises(FbctlError, match="changed after preflight"):
        bootstrap_host(
            runner=runner,
            root=root,
            source_env=_source(tmp_path / "source.env"),
            adoption_bundle=None,
            desktop_profile_seed=seed,
            docker_config=None,
            rehearsal=True,
        )

    assert runner.calls == []
    assert not (shared / "source.env").exists()
    assert not (root / "candidate").exists()


def test_locked_profile_recheck_fails_before_source_write_or_runner_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingRunner:
        def __init__(self) -> None:
            self.calls: list[object] = []

        def run(self, *args, **kwargs) -> None:
            self.calls.append((args, kwargs))
            raise AssertionError("runner must not be called")

    root = tmp_path / "fb-agent"
    shared = root / "shared"
    shared.mkdir(parents=True, mode=0o700)
    shared.chmod(0o700)
    seed = _write_profile(shared / "desktop-profile-seed")
    runner = RecordingRunner()
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(fbctl_controller, "bootstrap_profile_is_current", lambda _value: False)

    with pytest.raises(FbctlError, match="changed after preflight"):
        bootstrap_host(
            runner=runner,
            root=root,
            source_env=_source(tmp_path / "source.env"),
            adoption_bundle=None,
            desktop_profile_seed=seed,
            docker_config=None,
            rehearsal=True,
        )

    assert runner.calls == []
    assert not (shared / "source.env").exists()
    assert not (root / "candidate").exists()


def test_profile_entry_cap_aborts_before_opening_an_over_limit_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = tmp_path / "shared" / "vision-config"
    seed = _write_profile(tmp_path / "shared" / "desktop-profile-seed")
    monkeypatch.setattr(vision_profile, "MAX_PROFILE_ENTRIES", 2)
    monkeypatch.setattr(
        vision_profile,
        "_open_child_file",
        lambda *_args, **_kwargs: pytest.fail("over-limit names must abort before entry opens"),
    )

    with pytest.raises(FbctlError, match="exceeds safe bounds"):
        validate_bootstrap_vision_profile(
            canonical_profile=canonical,
            desktop_profile_seed=seed,
            seed_required_uid=os.getuid(),
            seed_required_gid=os.getgid(),
            canonical_required_uid=os.getuid(),
            canonical_required_gid=os.getgid(),
        )


def test_profile_total_byte_cap_stops_at_the_remaining_aggregate_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = tmp_path / "shared" / "vision-config"
    seed = _write_profile(tmp_path / "shared" / "desktop-profile-seed")
    preferences = seed / "browser" / "Preferences"
    preferences.write_bytes(b"x" * 128)
    preferences.chmod(0o600)
    target_inode = preferences.stat().st_ino
    original_read = vision_profile.os.read
    observed = 0

    def recording_read(descriptor: int, size: int) -> bytes:
        nonlocal observed
        payload = original_read(descriptor, size)
        if os.fstat(descriptor).st_ino == target_inode:
            observed += len(payload)
        return payload

    monkeypatch.setattr(
        vision_profile,
        "MAX_PROFILE_BYTES",
        len(VISION_PROFILE_MARKER_CONTENT) + 3,
    )
    monkeypatch.setattr(vision_profile.os, "read", recording_read)

    with pytest.raises(FbctlError, match="exceeds safe bounds"):
        validate_bootstrap_vision_profile(
            canonical_profile=canonical,
            desktop_profile_seed=seed,
            seed_required_uid=os.getuid(),
            seed_required_gid=os.getgid(),
            canonical_required_uid=os.getuid(),
            canonical_required_gid=os.getgid(),
        )

    assert observed == 4


def test_profile_depth_cap_rejects_nested_content(tmp_path: Path, monkeypatch) -> None:
    canonical = tmp_path / "shared" / "vision-config"
    seed = _write_profile(tmp_path / "shared" / "desktop-profile-seed")
    monkeypatch.setattr(vision_profile, "MAX_PROFILE_DEPTH", 0)

    with pytest.raises(FbctlError, match="exceeds safe bounds"):
        validate_bootstrap_vision_profile(
            canonical_profile=canonical,
            desktop_profile_seed=seed,
            seed_required_uid=os.getuid(),
            seed_required_gid=os.getgid(),
            canonical_required_uid=os.getuid(),
            canonical_required_gid=os.getgid(),
        )


def test_receipt_bound_copy_rejects_path_swap_without_publishing_canonical(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )
    os.rename(seed, shared / "original-seed")
    seed.symlink_to("/etc")

    with pytest.raises(FbctlError, match="changed before it was copied"):
        copy_profile_from_receipt(
            profile.active_receipt,
            canonical,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    assert not canonical.exists()
    assert list(shared.glob(".vision-config.*")) == []


def test_copy_failure_before_atomic_publish_leaves_no_canonical_or_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )
    monkeypatch.setattr(
        vision_profile,
        "_rename_noreplace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FbctlError("injected publish crash")),
    )

    with pytest.raises(FbctlError, match="injected publish crash"):
        copy_profile_from_receipt(
            profile.active_receipt,
            canonical,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    assert not canonical.exists()
    assert list(shared.glob(".vision-config.*")) == []
    assert seed.is_dir()


@pytest.mark.parametrize("failure", ["mk", "chown", "write", "fsync", "rename"])
def test_expected_copy_oserror_is_sanitized_and_cleans_private_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )

    def fail(*_args, **_kwargs):
        raise OSError("sensitive injected filesystem detail")

    if failure == "mk":
        monkeypatch.setattr(vision_profile.tempfile, "mkdtemp", fail)
    elif failure == "chown":
        monkeypatch.setattr(vision_profile.os, "fchown", fail)
    elif failure == "write":
        monkeypatch.setattr(vision_profile, "_write_all", fail)
    elif failure == "fsync":
        monkeypatch.setattr(vision_profile.os, "fsync", fail)
    else:
        monkeypatch.setattr(vision_profile, "_rename_noreplace", fail)

    with pytest.raises(FbctlError) as raised:
        copy_profile_from_receipt(
            profile.active_receipt,
            canonical,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    assert str(raised.value) == "managed Vision configuration could not be prepared safely"
    assert list(shared.glob(".vision-config.*")) == []
    assert not canonical.exists()
    assert seed.is_dir()


def test_copy_bounds_each_source_file_to_receipt_size_plus_one_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )
    preferences = next(
        entry
        for entry in profile.active_receipt.entries
        if entry.relative == ("browser", "Preferences")
    )
    original_read = vision_profile.os.read
    calls = 0

    def growing_read(descriptor: int, size: int) -> bytes:
        nonlocal calls
        if os.fstat(descriptor).st_ino != preferences.inode:
            return original_read(descriptor, size)
        calls += 1
        if calls == 1:
            return b"{}"
        if calls == 2:
            return b"x"
        raise AssertionError("read past receipt bound")

    monkeypatch.setattr(vision_profile.os, "read", growing_read)

    with pytest.raises(FbctlError, match="changed before it was copied"):
        copy_profile_from_receipt(
            profile.active_receipt,
            canonical,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    assert calls == 2
    assert not canonical.exists()
    assert list(shared.glob(".vision-config.*")) == []


def test_temp_identity_does_not_depend_on_path_lstat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )
    original_lstat = Path.lstat

    def fail_temp_lstat(path: Path, *args, **kwargs):
        if path.name.startswith(".vision-config."):
            raise OSError("sensitive temporary lstat failure")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fail_temp_lstat)

    published = copy_profile_from_receipt(
        profile.active_receipt,
        canonical,
        uid=os.getuid(),
        gid=os.getgid(),
    )

    assert published.path == canonical
    assert list(shared.glob(".vision-config.*")) == []
    assert canonical.is_dir()


def test_temp_open_failure_quarantines_unbound_path_without_deleting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )
    original_open = vision_profile.os.open

    def fail_temp_open(path, flags, *args, **kwargs):
        if isinstance(path, str) and path.startswith(".vision-config."):
            raise OSError("sensitive temporary open failure")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(vision_profile.os, "open", fail_temp_open)

    with pytest.raises(FbctlError) as raised:
        copy_profile_from_receipt(
            profile.active_receipt,
            canonical,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    assert str(raised.value) == "managed Vision configuration could not be prepared safely"
    assert list(shared.glob(".vision-config.*")) == []
    quarantines = list(shared.glob(".fbctl-profile-cleanup-unbound-*"))
    assert len(quarantines) == 1
    assert quarantines[0].is_dir()
    assert list(quarantines[0].iterdir()) == []
    assert not canonical.exists()


def test_copy_does_not_translate_programmer_errors_and_still_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )
    monkeypatch.setattr(
        vision_profile,
        "_copy_open_tree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("programmer bug")),
    )

    with pytest.raises(AssertionError, match="programmer bug"):
        copy_profile_from_receipt(
            profile.active_receipt,
            canonical,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    assert list(shared.glob(".vision-config.*")) == []
    assert not canonical.exists()


def test_copy_oserror_reaches_cli_as_structured_sanitized_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )
    monkeypatch.setattr(
        vision_profile,
        "_rename_noreplace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("sensitive injected /private/path")
        ),
    )
    monkeypatch.setattr(
        fbctl_main,
        "_dispatch",
        lambda _args: copy_profile_from_receipt(
            profile.active_receipt,
            canonical,
            uid=os.getuid(),
            gid=os.getgid(),
        ),
    )

    assert fbctl_main.main(["doctor"]) == 1

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload == {
        "error": "managed Vision configuration could not be prepared safely",
        "status": "FAILED",
        "step": None,
    }
    assert "Traceback" not in captured.err
    assert "private/path" not in captured.err
    assert list(shared.glob(".vision-config.*")) == []


def test_receipt_bound_copy_publishes_a_fully_valid_normalized_tree(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )

    published = copy_profile_from_receipt(
        profile.active_receipt,
        canonical,
        uid=os.getuid(),
        gid=os.getgid(),
    )

    assert published.path == canonical
    assert bootstrap_profile_is_current(
        replace(
            profile,
            active_receipt=published,
            canonical_receipt=published,
            seed_to_copy=None,
        )
    )
    assert (canonical / "browser" / "Preferences").read_text(encoding="utf-8") == "{}"


def test_copy_rejects_a_valid_path_substitution_after_atomic_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )
    original_publish = vision_profile._rename_noreplace

    def substitute_after_publish(*args, **kwargs) -> None:
        original_publish(*args, **kwargs)
        os.rename(canonical, shared / "published-original")
        replacement = _write_profile(canonical)
        (replacement / "browser" / "Preferences").write_text(
            "replacement",
            encoding="utf-8",
        )

    monkeypatch.setattr(vision_profile, "_rename_noreplace", substitute_after_publish)

    with pytest.raises(FbctlError, match="changed during publication"):
        copy_profile_from_receipt(
            profile.active_receipt,
            canonical,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    assert (canonical / "browser" / "Preferences").read_text(encoding="utf-8") == "replacement"
    assert (shared / "published-original").is_dir()


def test_canonical_retry_snapshots_and_cleans_only_the_original_leftover_seed(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    canonical = _write_profile(shared / "vision-config")
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )

    assert profile.seed_to_copy is None
    assert profile.seed_cleanup_receipt is not None
    assert remove_profile_tree_receipt(profile.seed_cleanup_receipt) is True
    assert canonical.is_dir()
    assert not seed.exists()


def test_seed_cleanup_preserves_a_path_substituted_before_quarantine(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )
    original = shared / "original-seed"
    os.rename(seed, original)
    replacement = _write_profile(seed)
    (replacement / "browser" / "Preferences").write_text("replacement", encoding="utf-8")

    assert profile.seed_cleanup_receipt is not None
    assert remove_profile_tree_receipt(profile.seed_cleanup_receipt) is False
    assert (seed / "browser" / "Preferences").read_text(encoding="utf-8") == "replacement"
    assert original.is_dir()


def test_seed_cleanup_quarantines_a_tree_substituted_during_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    canonical = shared / "vision-config"
    seed = _write_profile(shared / "desktop-profile-seed")
    profile = validate_bootstrap_vision_profile(
        canonical_profile=canonical,
        desktop_profile_seed=seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=os.getuid(),
        canonical_required_gid=os.getgid(),
    )
    original_rename = vision_profile.os.rename
    raced = False

    def racing_rename(source, destination, *args, **kwargs):
        nonlocal raced
        if not raced and source == seed.name and kwargs.get("src_dir_fd") is not None:
            original_rename(seed, shared / "original-seed")
            replacement = _write_profile(seed)
            (replacement / "browser" / "Preferences").write_text(
                "replacement",
                encoding="utf-8",
            )
            raced = True
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(vision_profile.os, "rename", racing_rename)

    assert profile.seed_cleanup_receipt is not None
    assert remove_profile_tree_receipt(profile.seed_cleanup_receipt) is False
    quarantines = list(shared.glob(".fbctl-profile-cleanup-*"))
    assert raced is True
    assert len(quarantines) == 1
    assert (quarantines[0] / "tree" / "browser" / "Preferences").read_text(
        encoding="utf-8"
    ) == "replacement"
