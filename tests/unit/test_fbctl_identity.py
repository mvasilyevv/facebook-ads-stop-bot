from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from fbctl import __main__ as fbctl_main
from fbctl import controller as fbctl_controller
from fbctl import files as fbctl_files
from fbctl.adoption import verify_adoption_bundle
from fbctl.bundle import BUNDLE_SCHEMA
from fbctl.controller import bootstrap_host
from fbctl.errors import FbctlError
from fbctl.files import MAX_DOTENV_BYTES, parse_dotenv
from fbctl.identity import (
    IDENTITY_KEYS,
    remove_legacy_identity,
    resolve_bootstrap_identity,
    snapshot_host_identity,
)
from fbctl.publish import publish
from fbctl.vision_profile import VISION_PROFILE_MARKER, VISION_PROFILE_MARKER_CONTENT

OIDC_CLIENT_ID = "111111"
OIDC_CLIENT_SECRET = "oidc-secret-" + "o" * 32
OWNER_TELEGRAM_USER_ID = "222222"
OTHER_OWNER_TELEGRAM_USER_ID = "999999"


@pytest.fixture(autouse=True)
def _local_vision_runtime_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fbctl_controller, "VISION_RUNTIME_UID", os.getuid())
    monkeypatch.setattr(fbctl_controller, "VISION_RUNTIME_GID", os.getgid())


def _write(path: Path, payload: bytes | str, *, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.name == "shared":
        path.parent.parent.chmod(0o755)
        path.parent.chmod(0o700)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    path.write_bytes(payload)
    path.chmod(mode)
    return path


def _identity_values(
    *,
    client_id: str = OIDC_CLIENT_ID,
    client_secret: str = OIDC_CLIENT_SECRET,
    owner: str = OWNER_TELEGRAM_USER_ID,
) -> dict[str, str]:
    return {
        "TELEGRAM_OIDC_CLIENT_ID": client_id,
        "TELEGRAM_OIDC_CLIENT_SECRET": client_secret,
        "DESKTOP_OWNER_TELEGRAM_USER_ID": owner,
    }


def _dotenv(values: dict[str, str]) -> bytes:
    return "".join(f"{key}={value}\n" for key, value in values.items()).encode("utf-8")


def _adoption_payload(owner: int = int(OWNER_TELEGRAM_USER_ID)) -> bytes:
    recipients = [
        {
            "chat_id": owner,
            "telegram_user_id": owner,
            "username": "owner",
            "display_name": "Owner",
            "role": "owner",
        }
    ]
    recipients_json = json.dumps(
        recipients,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    document = {
        "schema_version": "adoption-bundle/v1",
        "entity_counts": {"recipients": 1},
        "section_sha256": {"recipients": hashlib.sha256(recipients_json).hexdigest()},
        "sections": {"recipients": recipients},
    }
    return json.dumps(document, sort_keys=True).encode("utf-8") + b"\n"


def _bootstrap_source_without_identity(tmp_path: Path) -> Path:
    values = {
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(b"e" * 32).decode("ascii"),
        "ENCRYPTION_KEY_VERIFY": "verification-value",
        "TELEGRAM_BOT_TOKEN": "333333:test-token",
        "API_KEY": "a" * 32,
        "VISION_X_TOKEN": "vision-test-token",
        "VISION_PROFILE_ID": "profile-1",
        "PANEL_BASIC_AUTH_USER": "owner",
        "PANEL_BASIC_AUTH_HASH": "$2b$12$" + "h" * 53,
        "OPENAI_MODEL": "explicit-model",
    }
    return _write(tmp_path / "incoming-source.env", _dotenv(values))


def _legacy_root(
    tmp_path: Path,
    *,
    values: dict[str, str] | None = None,
    with_vision_config: bool = True,
) -> tuple[Path, Path]:
    root = tmp_path / "fb-agent"
    shared = root / "shared"
    shared.mkdir(parents=True)
    shared.chmod(0o700)
    if with_vision_config:
        vision_config = shared / "vision-config"
        vision_config.mkdir(mode=0o700)
        _write(vision_config / VISION_PROFILE_MARKER, VISION_PROFILE_MARKER_CONTENT)
    legacy = _write(
        shared / ".env",
        _dotenv(
            values
            or {
                **_identity_values(),
                "TELEGRAM_CHAT_ID": OTHER_OWNER_TELEGRAM_USER_ID,
                "REDIS_URL": "redis://legacy-value@example.invalid/0",
            }
        ),
    )
    return root, legacy


def _tree_state(root: Path) -> dict[str, tuple[object, ...]]:
    if not root.exists() and not root.is_symlink():
        return {}
    state: dict[str, tuple[object, ...]] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        metadata = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            state[relative] = ("symlink", mode, os.readlink(path))
        elif stat.S_ISREG(metadata.st_mode):
            state[relative] = ("file", mode, path.read_bytes())
        elif stat.S_ISDIR(metadata.st_mode):
            state[relative] = ("directory", mode)
        else:
            state[relative] = ("other", mode)
    return state


class _NoRemoteRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def run(self, command, **_kwargs):
        self.commands.append(tuple(os.fspath(part) for part in command))
        raise AssertionError("identity unit test reached an external command")


def _patch_bootstrap_runtime(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    fail_at: str | None = None,
    provision_hook=None,
    stage_observer=None,
    port_preflight_hook=None,
) -> dict[str, str | None]:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(fbctl_controller.sys, "version_info", (3, 12))
    failure = {"stage": fail_at}

    def materialize(destination: Path) -> dict[str, object]:
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        return {"release_id": "identity-test-release"}

    def prepare(**_kwargs):
        secret = root / "candidate" / "secrets" / "vision-bootstrap.env"
        return SimpleNamespace(
            layout=SimpleNamespace(base=root / "candidate"),
            values={"VISION_BOOTSTRAP_ENV_FILE": os.fspath(secret)},
        )

    def stage(name: str):
        def action(*_args, **_kwargs) -> None:
            if stage_observer is not None:
                stage_observer(name)
            if failure["stage"] == name:
                raise FbctlError(f"injected bootstrap failure at {name}")

        return action

    monkeypatch.setattr(fbctl_controller, "materialize_candidate", materialize)
    monkeypatch.setattr(fbctl_controller, "prepare_candidate", prepare)
    monkeypatch.setattr(
        fbctl_controller.ProductionController,
        "_require_available_infra_ports",
        (
            (lambda _controller, **kwargs: port_preflight_hook(**kwargs))
            if port_preflight_hook is not None
            else (lambda *_args, **_kwargs: None)
        ),
    )
    monkeypatch.setattr(
        fbctl_controller,
        "_normalize_profile_tree",
        lambda _path, *, uid, gid: None,
    )
    for method, name in (
        ("_preflight", "preflight"),
        ("_pull", "pull"),
        ("_ensure_bootstrap_resources", "resources"),
        ("_start_infra", "infra"),
        ("_migrate", "migrate"),
        ("_bootstrap_adoption", "adoption"),
        ("_bootstrap_runtime_config", "runtime_config"),
        ("_bootstrap_vision_config", "vision_config"),
    ):
        monkeypatch.setattr(fbctl_controller.ProductionController, method, stage(name))
    monkeypatch.setattr(
        fbctl_controller,
        "_provision_caddy",
        provision_hook or (lambda *_args, **_kwargs: None),
    )
    return failure


@pytest.mark.parametrize(
    "explicit,canonical,legacy,adoption_owner,expected_values,oidc_origin,owner_origin",
    [
        pytest.param(
            _identity_values(),
            _identity_values(client_id="333333", client_secret="c" * 40, owner="444444"),
            _identity_values(client_id="555555", client_secret="l" * 40, owner="666666"),
            OWNER_TELEGRAM_USER_ID,
            _identity_values(),
            "explicit",
            "explicit",
            id="explicit-wins",
        ),
        pytest.param(
            {"DESKTOP_OWNER_TELEGRAM_USER_ID": OWNER_TELEGRAM_USER_ID},
            {
                "TELEGRAM_OIDC_CLIENT_ID": OIDC_CLIENT_ID,
                "TELEGRAM_OIDC_CLIENT_SECRET": OIDC_CLIENT_SECRET,
            },
            _identity_values(client_id="555555", client_secret="l" * 40, owner="666666"),
            OWNER_TELEGRAM_USER_ID,
            _identity_values(),
            "canonical",
            "explicit",
            id="units-resolve-independently",
        ),
        pytest.param(
            {},
            {"DESKTOP_OWNER_TELEGRAM_USER_ID": OWNER_TELEGRAM_USER_ID},
            {
                "TELEGRAM_OIDC_CLIENT_ID": OIDC_CLIENT_ID,
                "TELEGRAM_OIDC_CLIENT_SECRET": OIDC_CLIENT_SECRET,
            },
            OWNER_TELEGRAM_USER_ID,
            _identity_values(),
            "legacy",
            "canonical",
            id="legacy-oidc-fallback",
        ),
        pytest.param(
            {},
            {},
            {
                "TELEGRAM_OIDC_CLIENT_ID": OIDC_CLIENT_ID,
                "TELEGRAM_OIDC_CLIENT_SECRET": OIDC_CLIENT_SECRET,
            },
            OWNER_TELEGRAM_USER_ID,
            _identity_values(),
            "legacy",
            "adoption",
            id="adoption-owner-last-fallback",
        ),
    ],
)
def test_identity_resolution_uses_precedence_per_atomic_unit(
    explicit: dict[str, str],
    canonical: dict[str, str],
    legacy: dict[str, str],
    adoption_owner: str,
    expected_values: dict[str, str],
    oidc_origin: str,
    owner_origin: str,
) -> None:
    resolved = resolve_bootstrap_identity(
        explicit=explicit,
        canonical=canonical,
        legacy=legacy,
        adoption_owner=adoption_owner,
        migration_enabled=True,
    )

    assert resolved.values == expected_values
    assert resolved.oidc_origin == oidc_origin
    assert resolved.owner_origin == owner_origin


@pytest.mark.parametrize(
    "explicit,canonical,legacy",
    [
        pytest.param(
            {"TELEGRAM_OIDC_CLIENT_ID": OIDC_CLIENT_ID},
            _identity_values(),
            {},
            id="partial-explicit-id",
        ),
        pytest.param(
            {"TELEGRAM_OIDC_CLIENT_SECRET": OIDC_CLIENT_SECRET},
            _identity_values(),
            {},
            id="partial-explicit-secret",
        ),
        pytest.param(
            {"DESKTOP_OWNER_TELEGRAM_USER_ID": OWNER_TELEGRAM_USER_ID},
            {"TELEGRAM_OIDC_CLIENT_ID": OIDC_CLIENT_ID},
            _identity_values(),
            id="partial-canonical",
        ),
        pytest.param(
            {},
            {},
            {"TELEGRAM_OIDC_CLIENT_SECRET": OIDC_CLIENT_SECRET},
            id="partial-legacy",
        ),
        pytest.param(
            {"TELEGRAM_OIDC_CLIENT_ID": ""},
            _identity_values(),
            {},
            id="blank-explicit-is-present",
        ),
    ],
)
def test_identity_resolution_rejects_partial_oidc_without_cross_source_splicing(
    explicit: dict[str, str],
    canonical: dict[str, str],
    legacy: dict[str, str],
) -> None:
    with pytest.raises(FbctlError):
        resolve_bootstrap_identity(
            explicit=explicit,
            canonical=canonical,
            legacy=legacy,
            adoption_owner=OWNER_TELEGRAM_USER_ID,
            migration_enabled=True,
        )


@pytest.mark.parametrize(
    "explicit,canonical,legacy,adoption_owner",
    [
        pytest.param(
            _identity_values(client_id="not-numeric"), {}, {}, OWNER_TELEGRAM_USER_ID, id="id"
        ),
        pytest.param(
            _identity_values(client_secret="short"),
            {},
            {},
            OWNER_TELEGRAM_USER_ID,
            id="secret",
        ),
        pytest.param(_identity_values(owner="0"), {}, {}, OWNER_TELEGRAM_USER_ID, id="owner"),
        pytest.param(
            {
                "TELEGRAM_OIDC_CLIENT_ID": OIDC_CLIENT_ID,
                "TELEGRAM_OIDC_CLIENT_SECRET": OIDC_CLIENT_SECRET,
            },
            {"DESKTOP_OWNER_TELEGRAM_USER_ID": "not-numeric"},
            {"DESKTOP_OWNER_TELEGRAM_USER_ID": OWNER_TELEGRAM_USER_ID},
            OWNER_TELEGRAM_USER_ID,
            id="invalid-high-priority-owner",
        ),
    ],
)
def test_identity_resolution_rejects_invalid_high_priority_identity_without_fallback(
    explicit: dict[str, str],
    canonical: dict[str, str],
    legacy: dict[str, str],
    adoption_owner: str,
) -> None:
    with pytest.raises(FbctlError):
        resolve_bootstrap_identity(
            explicit=explicit,
            canonical=canonical,
            legacy=legacy,
            adoption_owner=adoption_owner,
            migration_enabled=True,
        )


def test_identity_resolution_ignores_invalid_lower_priority_source_after_complete_choice() -> None:
    resolved = resolve_bootstrap_identity(
        explicit=_identity_values(),
        canonical={"TELEGRAM_OIDC_CLIENT_ID": "partial-is-not-selected"},
        legacy={"DESKTOP_OWNER_TELEGRAM_USER_ID": "invalid-lower-owner"},
        adoption_owner=OWNER_TELEGRAM_USER_ID,
        migration_enabled=True,
    )

    assert resolved.values == _identity_values()
    assert resolved.oidc_origin == "explicit"
    assert resolved.owner_origin == "explicit"


def test_identity_resolution_rejects_adoption_owner_mismatch() -> None:
    with pytest.raises(FbctlError):
        resolve_bootstrap_identity(
            explicit=_identity_values(),
            canonical={},
            legacy={},
            adoption_owner=OTHER_OWNER_TELEGRAM_USER_ID,
            migration_enabled=True,
        )


def test_identity_resolution_ignores_chat_and_unrelated_legacy_values() -> None:
    legacy = {
        **_identity_values(),
        "TELEGRAM_CHAT_ID": OTHER_OWNER_TELEGRAM_USER_ID,
        "TELEGRAM_BOT_TOKEN": "333333:must-not-become-an-identity",
        "REDIS_URL": "redis://legacy-secret@example.invalid/0",
    }

    resolved = resolve_bootstrap_identity(
        explicit={},
        canonical={},
        legacy=legacy,
        adoption_owner=OWNER_TELEGRAM_USER_ID,
        migration_enabled=True,
    )

    assert resolved.values == _identity_values()
    assert (
        resolved.values["TELEGRAM_OIDC_CLIENT_ID"]
        != resolved.values["DESKTOP_OWNER_TELEGRAM_USER_ID"]
    )
    assert "TELEGRAM_CHAT_ID" not in resolved.values
    assert "TELEGRAM_BOT_TOKEN" not in resolved.values
    assert "REDIS_URL" not in resolved.values


def test_identity_resolution_never_logs_source_values_in_errors() -> None:
    sentinel = "never-print-this-identity-secret"

    with pytest.raises(FbctlError) as raised:
        resolve_bootstrap_identity(
            explicit={"TELEGRAM_OIDC_CLIENT_SECRET": sentinel},
            canonical=_identity_values(),
            legacy={},
            adoption_owner=OWNER_TELEGRAM_USER_ID,
            migration_enabled=True,
        )

    assert sentinel not in str(raised.value)


def test_identity_resolution_disables_legacy_and_adoption_fallback_without_migration() -> None:
    with pytest.raises(FbctlError):
        resolve_bootstrap_identity(
            explicit={},
            canonical={},
            legacy=_identity_values(),
            adoption_owner=OWNER_TELEGRAM_USER_ID,
            migration_enabled=False,
        )


@pytest.mark.parametrize(
    "explicit,canonical,legacy,migration_enabled,expected",
    [
        pytest.param(_identity_values(), {}, _identity_values(), True, False, id="explicit"),
        pytest.param({}, _identity_values(), _identity_values(), True, True, id="retry"),
        pytest.param({}, {}, _identity_values(), True, True, id="legacy"),
        pytest.param(_identity_values(), {}, _identity_values(), False, False, id="disabled"),
        pytest.param(_identity_values(), {}, {}, True, False, id="legacy-absent"),
    ],
)
def test_identity_resolution_marks_only_migration_legacy_for_post_success_cleanup(
    explicit: dict[str, str],
    canonical: dict[str, str],
    legacy: dict[str, str],
    migration_enabled: bool,
    expected: bool,
) -> None:
    resolved = resolve_bootstrap_identity(
        explicit=explicit,
        canonical=canonical,
        legacy=legacy,
        adoption_owner=OWNER_TELEGRAM_USER_ID,
        migration_enabled=migration_enabled,
    )

    assert resolved.legacy_cleanup_eligible is expected


def test_host_identity_snapshot_reads_canonical_legacy_and_verified_adoption(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fb-agent"
    canonical_payload = _dotenv(
        _identity_values(client_id="333333", client_secret="c" * 40, owner="444444")
    )
    legacy_payload = _dotenv(
        {
            **_identity_values(),
            "TELEGRAM_CHAT_ID": OTHER_OWNER_TELEGRAM_USER_ID,
            "UNRELATED_LEGACY_SECRET": "must-not-be-projected",
        }
    )
    canonical = _write(root / "shared" / "source.env", canonical_payload)
    legacy = _write(root / "shared" / ".env", legacy_payload)
    adoption_payload = _adoption_payload(int(OWNER_TELEGRAM_USER_ID))
    adoption = _write(root / "shared" / "adoption-bundle-v1.json", adoption_payload)

    snapshot = snapshot_host_identity(
        root,
        adoption_bundle=adoption,
        required_uid=os.geteuid(),
    )

    assert snapshot.canonical_values == {
        "TELEGRAM_OIDC_CLIENT_ID": "333333",
        "TELEGRAM_OIDC_CLIENT_SECRET": "c" * 40,
        "DESKTOP_OWNER_TELEGRAM_USER_ID": "444444",
    }
    assert snapshot.legacy_values == {
        **_identity_values(),
        "TELEGRAM_CHAT_ID": OTHER_OWNER_TELEGRAM_USER_ID,
        "UNRELATED_LEGACY_SECRET": "must-not-be-projected",
    }
    assert snapshot.canonical_snapshot is not None
    assert snapshot.canonical_snapshot.path == canonical
    assert snapshot.canonical_snapshot.payload == canonical_payload
    assert snapshot.legacy_snapshot is not None
    assert snapshot.legacy_snapshot.path == legacy
    assert snapshot.legacy_snapshot.payload == legacy_payload
    assert snapshot.adoption is not None
    assert snapshot.adoption.owner_telegram_user_id == OWNER_TELEGRAM_USER_ID
    assert snapshot.adoption.payload == adoption_payload


@pytest.mark.parametrize(
    "case",
    ["mode", "symlink", "hardlink", "non-regular", "wrong-owner"],
)
def test_host_identity_snapshot_rejects_unsafe_legacy_file(
    tmp_path: Path,
    case: str,
) -> None:
    root = tmp_path / "fb-agent"
    legacy = root / "shared" / ".env"
    payload = _dotenv(_identity_values())
    legacy.parent.mkdir(parents=True)
    required_uid = os.geteuid()
    if case == "mode":
        _write(legacy, payload, mode=0o640)
    elif case == "symlink":
        legacy.symlink_to(_write(tmp_path / "legacy-target.env", payload))
    elif case == "hardlink":
        source = _write(tmp_path / "legacy-source.env", payload)
        os.link(source, legacy)
    elif case == "non-regular":
        legacy.mkdir()
        legacy.chmod(0o600)
    else:
        _write(legacy, payload)
        required_uid = os.geteuid() + 1

    with pytest.raises(FbctlError):
        snapshot_host_identity(root, required_uid=required_uid)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"\xff\xfe", id="invalid-utf8"),
        pytest.param(_dotenv(_identity_values()) + b"NUL=before\x00after\n", id="nul"),
        pytest.param(
            _dotenv(_identity_values()) + b"TELEGRAM_OIDC_CLIENT_ID=333333\n",
            id="duplicate-identity",
        ),
        pytest.param(
            _dotenv(_identity_values()) + b"IGNORED=one\nIGNORED=two\n",
            id="duplicate-unrelated",
        ),
        pytest.param(_dotenv(_identity_values()) + b"not-dotenv\n", id="malformed"),
    ],
)
def test_host_identity_snapshot_rejects_invalid_legacy_dotenv(
    tmp_path: Path,
    payload: bytes,
) -> None:
    root = tmp_path / "fb-agent"
    _write(root / "shared" / ".env", payload)

    with pytest.raises(FbctlError):
        snapshot_host_identity(root, required_uid=os.geteuid())


def test_host_identity_snapshot_treats_empty_legacy_file_as_no_identity(tmp_path: Path) -> None:
    root = tmp_path / "fb-agent"
    _write(root / "shared" / ".env", b"")

    snapshot = snapshot_host_identity(root, required_uid=os.geteuid())

    assert snapshot.legacy_values == {}
    assert snapshot.legacy_snapshot is not None


def test_host_identity_snapshot_accepts_exact_size_limit(tmp_path: Path) -> None:
    root = tmp_path / "fb-agent"
    identity = _dotenv(_identity_values())
    padding_size = MAX_DOTENV_BYTES - len(identity)
    assert padding_size >= 2
    payload = identity + b"#" + b"x" * (padding_size - 2) + b"\n"
    assert len(payload) == MAX_DOTENV_BYTES
    _write(root / "shared" / ".env", payload)

    snapshot = snapshot_host_identity(root, required_uid=os.geteuid())

    assert snapshot.legacy_values == _identity_values()
    assert snapshot.legacy_snapshot is not None
    assert snapshot.legacy_snapshot.size == MAX_DOTENV_BYTES


def test_host_identity_snapshot_rejects_over_size_limit(tmp_path: Path) -> None:
    root = tmp_path / "fb-agent"
    _write(root / "shared" / ".env", b"x" * (MAX_DOTENV_BYTES + 1))

    with pytest.raises(FbctlError):
        snapshot_host_identity(root, required_uid=os.geteuid())


def test_host_identity_snapshot_opens_legacy_without_following_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "fb-agent"
    legacy = _write(root / "shared" / ".env", _dotenv(_identity_values()))
    original_open = fbctl_files.os.open
    observed_flags: list[int] = []

    def recording_open(path, flags, *args, **kwargs):
        if Path(path) == legacy or (
            Path(path) == Path(".env") and kwargs.get("dir_fd") is not None
        ):
            observed_flags.append(flags)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(fbctl_files.os, "open", recording_open)

    snapshot_host_identity(root, required_uid=os.geteuid())

    assert len(observed_flags) == 1
    assert observed_flags[0] & getattr(os, "O_NOFOLLOW", 0)
    assert observed_flags[0] & getattr(os, "O_CLOEXEC", 0)
    assert observed_flags[0] & getattr(os, "O_NONBLOCK", 0)


def test_remove_legacy_identity_deletes_only_the_snapshotted_inode(tmp_path: Path) -> None:
    root = tmp_path / "fb-agent"
    legacy = _write(root / "shared" / ".env", _dotenv(_identity_values()))
    snapshot = snapshot_host_identity(root, required_uid=os.geteuid())

    assert remove_legacy_identity(snapshot) is True
    assert not legacy.exists()
    assert remove_legacy_identity(snapshot) is False


def test_remove_legacy_identity_preserves_replaced_path(tmp_path: Path) -> None:
    root = tmp_path / "fb-agent"
    legacy = _write(root / "shared" / ".env", _dotenv(_identity_values()))
    snapshot = snapshot_host_identity(root, required_uid=os.geteuid())
    replacement_payload = _dotenv(
        _identity_values(client_id="333333", client_secret="replacement-" + "r" * 32)
    )
    replacement = _write(root / "shared" / ".env.next", replacement_payload)
    os.replace(replacement, legacy)

    assert remove_legacy_identity(snapshot) is False
    assert legacy.read_bytes() == replacement_payload


def test_snapshot_cleanup_restores_replacement_raced_at_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write(tmp_path / "source.env", b"snapshotted\n")
    snapshot = fbctl_files.snapshot_private_file(
        source,
        label="source environment",
        maximum=1024,
        required_uid=os.geteuid(),
    )
    assert snapshot is not None
    replacement_payload = b"replacement\n"
    original_stat = fbctl_files.os.stat
    original_rename = fbctl_files.os.rename
    raced = False
    replacement_inode: int | None = None

    def install_replacement() -> None:
        nonlocal raced, replacement_inode
        if raced:
            return
        replacement = _write(tmp_path / "source.env.next", replacement_payload)
        replacement_inode = original_stat(replacement).st_ino
        os.replace(replacement, source)
        raced = True

    def racing_stat(path, *args, **kwargs):
        current = original_stat(path, *args, **kwargs)
        if path == source.name and kwargs.get("dir_fd") is not None:
            install_replacement()
        return current

    def racing_rename(src, dst, *args, **kwargs):
        if src == source.name and kwargs.get("src_dir_fd") is not None:
            install_replacement()
        return original_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(fbctl_files.os, "stat", racing_stat)
    monkeypatch.setattr(fbctl_files.os, "rename", racing_rename)

    assert fbctl_files.unlink_unchanged_snapshot(snapshot) is False
    assert raced is True
    assert source.read_bytes() == replacement_payload
    assert original_stat(source).st_ino == replacement_inode
    quarantines = list(tmp_path.glob(".fbctl-cleanup-*"))
    assert len(quarantines) == 1
    quarantined = quarantines[0] / "snapshot"
    assert quarantined.read_bytes() == replacement_payload
    assert original_stat(quarantined).st_ino == replacement_inode


def test_snapshot_cleanup_stat_error_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write(tmp_path / "source.env", b"snapshotted\n")
    snapshot = fbctl_files.snapshot_private_file(
        source,
        label="source environment",
        maximum=1024,
        required_uid=os.geteuid(),
    )
    assert snapshot is not None
    original_stat = fbctl_files.os.stat

    def denied_stat(path, *args, **kwargs):
        if path == source.name and kwargs.get("dir_fd") is not None:
            raise PermissionError("injected stat denial")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(fbctl_files.os, "stat", denied_stat)

    assert fbctl_files.unlink_unchanged_snapshot(snapshot) is False
    assert source.read_bytes() == b"snapshotted\n"
    assert list(tmp_path.glob(".fbctl-cleanup-*")) == []


def test_snapshot_cleanup_never_overwrites_concurrent_restore_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write(tmp_path / "source.env", b"snapshotted\n")
    snapshot = fbctl_files.snapshot_private_file(
        source,
        label="source environment",
        maximum=1024,
        required_uid=os.geteuid(),
    )
    assert snapshot is not None
    moved_payload = b"replacement-moved-to-quarantine\n"
    concurrent_payload = b"concurrent-current-path\n"
    original_rename = fbctl_files.os.rename
    original_link = fbctl_files.os.link
    raced = False

    def racing_rename(src, dst, *args, **kwargs):
        nonlocal raced
        if not raced and src == source.name and kwargs.get("src_dir_fd") is not None:
            replacement = _write(tmp_path / "source.env.next", moved_payload)
            os.replace(replacement, source)
            raced = True
        return original_rename(src, dst, *args, **kwargs)

    def racing_link(src, dst, *args, **kwargs):
        if dst == source.name and kwargs.get("dst_dir_fd") is not None:
            _write(source, concurrent_payload)
        return original_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(fbctl_files.os, "rename", racing_rename)
    monkeypatch.setattr(fbctl_files.os, "link", racing_link)

    assert fbctl_files.unlink_unchanged_snapshot(snapshot) is False
    assert raced is True
    assert source.read_bytes() == concurrent_payload
    quarantines = list(tmp_path.glob(".fbctl-cleanup-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "snapshot").read_bytes() == moved_payload


def test_snapshot_cleanup_preserves_quarantine_when_restored_path_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write(tmp_path / "source.env", b"snapshotted\n")
    snapshot = fbctl_files.snapshot_private_file(
        source,
        label="source environment",
        maximum=1024,
        required_uid=os.geteuid(),
    )
    assert snapshot is not None
    moved_payload = b"replacement-moved-to-quarantine\n"
    concurrent_payload = b"replacement-after-restore-link\n"
    original_rename = fbctl_files.os.rename
    original_link = fbctl_files.os.link
    raced = False

    def racing_rename(src, dst, *args, **kwargs):
        nonlocal raced
        if not raced and src == source.name and kwargs.get("src_dir_fd") is not None:
            replacement = _write(tmp_path / "source.env.next", moved_payload)
            os.replace(replacement, source)
            raced = True
        return original_rename(src, dst, *args, **kwargs)

    def replace_after_link(src, dst, *args, **kwargs):
        result = original_link(src, dst, *args, **kwargs)
        replacement = _write(tmp_path / "source.env.concurrent", concurrent_payload)
        os.replace(replacement, source)
        return result

    monkeypatch.setattr(fbctl_files.os, "rename", racing_rename)
    monkeypatch.setattr(fbctl_files.os, "link", replace_after_link)

    assert fbctl_files.unlink_unchanged_snapshot(snapshot) is False
    assert raced is True
    assert source.read_bytes() == concurrent_payload
    quarantines = list(tmp_path.glob(".fbctl-cleanup-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "snapshot").read_bytes() == moved_payload


def test_remove_legacy_identity_preserves_inode_that_became_hardlinked(tmp_path: Path) -> None:
    root = tmp_path / "fb-agent"
    legacy = _write(root / "shared" / ".env", _dotenv(_identity_values()))
    snapshot = snapshot_host_identity(root, required_uid=os.geteuid())
    alias = root / "shared" / ".env.alias"
    os.link(legacy, alias)

    assert remove_legacy_identity(snapshot) is False
    assert legacy.exists()
    assert alias.exists()


def test_bootstrap_migrates_only_identity_from_fixed_legacy_source_after_full_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, legacy = _legacy_root(tmp_path)
    source = _bootstrap_source_without_identity(tmp_path)
    runner = _NoRemoteRunner()
    observed_stages: list[str] = []

    def observe(stage: str) -> None:
        observed_stages.append(stage)
        assert legacy.exists(), f"legacy identity was consumed before {stage} completed"

    def provision(*_args, **_kwargs) -> None:
        observe("caddy")

    def retire_systemd(_runner) -> list[str]:
        observe("systemd_retirement")
        return ["vision-token-refresh.timer"]

    _patch_bootstrap_runtime(
        monkeypatch,
        root,
        provision_hook=provision,
        stage_observer=observe,
    )
    monkeypatch.setattr(
        fbctl_controller,
        "_retire_legacy_systemd_units",
        retire_systemd,
    )

    result = bootstrap_host(
        runner=runner,
        root=root,
        source_env=source,
        adoption_bundle=None,
        desktop_profile_seed=None,
        docker_config=None,
        migrate_existing_bootstrap_identity=True,
    )

    canonical_path = root / "shared" / "source.env"
    canonical = parse_dotenv(canonical_path)
    assert result["status"] == "READY"
    assert result["legacy_identity_cleanup"] == "removed"
    assert result["retired_systemd_units"] == ["vision-token-refresh.timer"]
    assert observed_stages == [
        "preflight",
        "pull",
        "resources",
        "infra",
        "migrate",
        "adoption",
        "runtime_config",
        "vision_config",
        "caddy",
        "systemd_retirement",
    ]
    assert {key: canonical[key] for key in IDENTITY_KEYS} == _identity_values()
    assert canonical["OPENAI_MODEL"] == "explicit-model"
    assert "TELEGRAM_CHAT_ID" not in canonical
    assert "REDIS_URL" not in canonical
    assert stat.S_IMODE(canonical_path.stat().st_mode) == 0o600
    assert not legacy.exists()
    assert runner.commands == []


def test_bootstrap_port_collision_fails_before_host_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _legacy = _legacy_root(tmp_path)
    source = _bootstrap_source_without_identity(tmp_path)
    runner = _NoRemoteRunner()
    observed_values: dict[str, str] = {}

    def reject_port_collision(*, values, docker_config) -> None:
        assert docker_config is None
        observed_values.update(values)
        raise FbctlError(
            "Docker host port collision: POSTGRES_HOST_PORT=5433 is occupied by container "
            "legacy-postgres; stop it manually before retrying: sudo docker stop legacy-postgres"
        )

    _patch_bootstrap_runtime(
        monkeypatch,
        root,
        port_preflight_hook=reject_port_collision,
    )
    before = _tree_state(root)

    with pytest.raises(FbctlError, match="POSTGRES_HOST_PORT=5433"):
        bootstrap_host(
            runner=runner,
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=None,
            docker_config=None,
            migrate_existing_bootstrap_identity=True,
        )

    assert observed_values["INFRA_PROJECT_NAME"] == "fb_agent_infra"
    assert observed_values["POSTGRES_HOST_PORT"] == "5433"
    assert observed_values["REDIS_HOST_PORT"] == "6380"
    # Порт-гейт стоит внутри deployment lock, после перепроверки identity и
    # Vision profile: иначе внешний вызов случился бы раньше, чем мы убедились,
    # что снимок не подменили.  Поэтому shared-директория и файл лока к этому
    # моменту уже созданы, а вот durable identity и Docker — ещё нетронуты.
    assert not (root / "shared" / "source.env").exists()
    assert _tree_state(root / "candidate") == before.get("candidate", {})
    assert runner.commands == []


def test_bootstrap_failure_persists_canonical_identity_then_retry_prefers_it_and_cleans_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, legacy = _legacy_root(tmp_path)
    source = _bootstrap_source_without_identity(tmp_path)
    runner = _NoRemoteRunner()
    real_resolver = fbctl_controller.resolve_bootstrap_identity
    origins: list[tuple[str, str]] = []

    def recording_resolver(**kwargs):
        result = real_resolver(**kwargs)
        origins.append((result.oidc_origin, result.owner_origin))
        return result

    failure = _patch_bootstrap_runtime(monkeypatch, root, fail_at="resources")
    monkeypatch.setattr(fbctl_controller, "resolve_bootstrap_identity", recording_resolver)

    with pytest.raises(FbctlError, match="injected bootstrap failure"):
        bootstrap_host(
            runner=runner,
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=None,
            docker_config=None,
            migrate_existing_bootstrap_identity=True,
        )

    first_canonical = parse_dotenv(root / "shared" / "source.env")
    assert {key: first_canonical[key] for key in IDENTITY_KEYS} == _identity_values()
    assert legacy.exists()
    assert origins == [("legacy", "legacy")]

    failure["stage"] = None
    result = bootstrap_host(
        runner=runner,
        root=root,
        source_env=source,
        adoption_bundle=None,
        desktop_profile_seed=None,
        docker_config=None,
        migrate_existing_bootstrap_identity=True,
    )

    assert result["status"] == "READY"
    assert result["legacy_identity_cleanup"] == "removed"
    assert origins == [("legacy", "legacy"), ("canonical", "canonical")]
    assert parse_dotenv(root / "shared" / "source.env") == first_canonical
    assert not legacy.exists()
    assert runner.commands == []


@pytest.mark.parametrize(
    "case",
    [
        "partial-legacy",
        "invalid-legacy",
        "unsafe-legacy",
        "adoption-mismatch",
        "unsafe-adoption",
    ],
)
def test_bootstrap_rejects_invalid_host_identity_before_root_or_runner_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    root, legacy = _legacy_root(tmp_path)
    source = _bootstrap_source_without_identity(tmp_path)
    adoption: Path | None = None
    leak_sentinel = "do-not-leak-this-value"
    if case == "partial-legacy":
        _write(
            legacy,
            _dotenv(
                {
                    "TELEGRAM_OIDC_CLIENT_ID": OIDC_CLIENT_ID,
                    "DESKTOP_OWNER_TELEGRAM_USER_ID": OWNER_TELEGRAM_USER_ID,
                }
            ),
        )
    elif case == "invalid-legacy":
        _write(
            legacy,
            _dotenv(_identity_values(client_secret=leak_sentinel)),
        )
    elif case == "unsafe-legacy":
        legacy.chmod(0o640)
    elif case == "adoption-mismatch":
        adoption = _write(
            root / "shared" / "adoption-bundle-v1.json",
            _adoption_payload(int(OTHER_OWNER_TELEGRAM_USER_ID)),
        )
    else:
        adoption_source = _write(tmp_path / "adoption-source.json", _adoption_payload())
        adoption = root / "shared" / "adoption-bundle-v1.json"
        os.link(adoption_source, adoption)
    before = _tree_state(root)
    runner = _NoRemoteRunner()
    _patch_bootstrap_runtime(monkeypatch, root)

    with pytest.raises(FbctlError) as raised:
        bootstrap_host(
            runner=runner,
            root=root,
            source_env=source,
            adoption_bundle=adoption,
            desktop_profile_seed=None,
            docker_config=None,
            migrate_existing_bootstrap_identity=True,
        )

    assert _tree_state(root) == before
    assert runner.commands == []
    assert leak_sentinel not in str(raised.value)


def test_bootstrap_preserves_replaced_legacy_inode_and_reports_changed_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, legacy = _legacy_root(tmp_path)
    source = _bootstrap_source_without_identity(tmp_path)
    replacement_payload = _dotenv({**_identity_values(), "REPLACED": "after-preflight"})

    def replace_before_cleanup(*_args, **_kwargs) -> None:
        replacement = _write(root / "shared" / ".env.next", replacement_payload)
        os.replace(replacement, legacy)

    _patch_bootstrap_runtime(monkeypatch, root, provision_hook=replace_before_cleanup)
    runner = _NoRemoteRunner()

    result = bootstrap_host(
        runner=runner,
        root=root,
        source_env=source,
        adoption_bundle=None,
        desktop_profile_seed=None,
        docker_config=None,
        migrate_existing_bootstrap_identity=True,
    )

    assert result["status"] == "READY"
    assert result["legacy_identity_cleanup"] == "preserved_changed"
    assert legacy.read_bytes() == replacement_payload
    assert runner.commands == []


def test_bootstrap_without_migration_flag_does_not_use_legacy_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, legacy = _legacy_root(tmp_path)
    source = _bootstrap_source_without_identity(tmp_path)
    before = _tree_state(root)
    runner = _NoRemoteRunner()
    _patch_bootstrap_runtime(monkeypatch, root)

    with pytest.raises(FbctlError):
        bootstrap_host(
            runner=runner,
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=None,
            docker_config=None,
            migrate_existing_bootstrap_identity=False,
        )

    assert legacy.exists()
    assert _tree_state(root) == before
    assert runner.commands == []


def test_bootstrap_without_migration_flag_never_reads_unsafe_legacy_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, legacy = _legacy_root(tmp_path)
    legacy.chmod(0o644)
    source = _bootstrap_source_without_identity(tmp_path)
    with source.open("ab") as handle:
        handle.write(_dotenv(_identity_values()))
    source.chmod(0o600)
    runner = _NoRemoteRunner()
    _patch_bootstrap_runtime(monkeypatch, root)

    result = bootstrap_host(
        runner=runner,
        root=root,
        source_env=source,
        adoption_bundle=None,
        desktop_profile_seed=None,
        docker_config=None,
        migrate_existing_bootstrap_identity=False,
    )

    assert result["status"] == "READY"
    assert legacy.exists()
    assert stat.S_IMODE(legacy.stat().st_mode) == 0o644
    assert runner.commands == []


def test_deployment_lock_rejects_symlink_without_changing_target_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "fb-agent"
    shared = root / "shared"
    shared.mkdir(parents=True)
    root.chmod(0o755)
    shared.chmod(0o700)
    victim = _write(tmp_path / "unrelated.conf", b"unrelated\n", mode=0o644)
    (shared / "deploy.lock").symlink_to(victim)
    controller = fbctl_controller.ProductionController(runner=_NoRemoteRunner())
    original_open = fbctl_controller.os.open
    observed: list[tuple[int, int | None]] = []

    def recording_open(path, flags, *args, **kwargs):
        if path == "deploy.lock":
            observed.append((flags, kwargs.get("dir_fd")))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(fbctl_controller.os, "open", recording_open)

    with pytest.raises(FbctlError, match="deployment lock path is unsafe"):
        with controller._deployment_lock(root):  # noqa: SLF001 - security regression
            raise AssertionError("unsafe lock was acquired")

    assert victim.read_bytes() == b"unrelated\n"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert len(observed) == 1
    assert observed[0][0] & getattr(os, "O_NOFOLLOW", 0)
    assert observed[0][0] & getattr(os, "O_CLOEXEC", 0)
    assert observed[0][1] is not None


def test_deployment_lock_creation_is_independent_of_host_umask(tmp_path: Path) -> None:
    root = tmp_path / "fb-agent"
    shared = root / "shared"
    shared.mkdir(parents=True)
    root.chmod(0o755)
    shared.chmod(0o700)
    controller = fbctl_controller.ProductionController(runner=_NoRemoteRunner())
    previous_umask = os.umask(0o777)
    try:
        with controller._deployment_lock(root):  # noqa: SLF001 - security regression
            lock = shared / "deploy.lock"
            assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    finally:
        os.umask(previous_umask)


@pytest.mark.parametrize("case", ["mode", "hardlink"])
def test_deployment_lock_rejects_unsafe_existing_inode_without_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    root = tmp_path / "fb-agent"
    shared = root / "shared"
    shared.mkdir(parents=True)
    root.chmod(0o755)
    shared.chmod(0o700)
    lock = _write(shared / "deploy.lock", b"", mode=0o640 if case == "mode" else 0o600)
    alias = shared / "deploy.lock.alias"
    if case == "hardlink":
        os.link(lock, alias)
    before = lock.stat()
    fchmod_calls: list[tuple[int, int]] = []
    original_fchmod = fbctl_controller.os.fchmod

    def recording_fchmod(descriptor: int, mode: int) -> None:
        fchmod_calls.append((descriptor, mode))
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(fbctl_controller.os, "fchmod", recording_fchmod)
    controller = fbctl_controller.ProductionController(runner=_NoRemoteRunner())

    with pytest.raises(FbctlError, match="deployment lock file is unsafe"):
        with controller._deployment_lock(root):  # noqa: SLF001 - security regression
            raise AssertionError("unsafe lock was acquired")

    after = lock.stat()
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert after.st_nlink == before.st_nlink
    assert fchmod_calls == []
    assert alias.exists() is (case == "hardlink")


def test_deployment_lock_rejects_concurrent_holder(tmp_path: Path) -> None:
    root = tmp_path / "fb-agent"
    shared = root / "shared"
    shared.mkdir(parents=True)
    root.chmod(0o755)
    shared.chmod(0o700)
    first = fbctl_controller.ProductionController(runner=_NoRemoteRunner())
    second = fbctl_controller.ProductionController(runner=_NoRemoteRunner())

    with first._deployment_lock(root):  # noqa: SLF001 - concurrency regression
        with pytest.raises(FbctlError, match="another fbctl deployment is running"):
            with second._deployment_lock(root):  # noqa: SLF001 - concurrency regression
                raise AssertionError("concurrent lock was acquired")


@pytest.mark.parametrize("component", ["root", "shared"])
def test_deployment_lock_rejects_untrusted_host_directory_mode(
    tmp_path: Path,
    component: str,
) -> None:
    root = tmp_path / "fb-agent"
    shared = root / "shared"
    shared.mkdir(parents=True)
    root.chmod(0o700 if component == "root" else 0o755)
    shared.chmod(0o755 if component == "shared" else 0o700)
    controller = fbctl_controller.ProductionController(runner=_NoRemoteRunner())

    with pytest.raises(FbctlError, match="owned directory with mode"):
        with controller._deployment_lock(root):  # noqa: SLF001 - security regression
            raise AssertionError("lock under an untrusted directory was acquired")

    assert not (shared / "deploy.lock").exists()
    assert stat.S_IMODE(root.stat().st_mode) == (0o700 if component == "root" else 0o755)
    assert stat.S_IMODE(shared.stat().st_mode) == (0o755 if component == "shared" else 0o700)


def test_deployment_lock_rejects_untrusted_host_directory_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "fb-agent"
    shared = root / "shared"
    shared.mkdir(parents=True)
    root.chmod(0o755)
    shared.chmod(0o700)
    root_inode = root.stat().st_ino
    original_fstat = fbctl_files.os.fstat

    def wrong_root_owner(descriptor: int):
        metadata = original_fstat(descriptor)
        if metadata.st_ino != root_inode:
            return metadata
        fields = list(metadata)
        fields[4] = metadata.st_uid + 1
        return os.stat_result(fields)

    monkeypatch.setattr(fbctl_files.os, "fstat", wrong_root_owner)
    controller = fbctl_controller.ProductionController(runner=_NoRemoteRunner())

    with pytest.raises(FbctlError, match="owned directory with mode"):
        with controller._deployment_lock(root):  # noqa: SLF001 - security regression
            raise AssertionError("lock under a non-owned root was acquired")

    assert not (shared / "deploy.lock").exists()


def test_verified_adoption_bundle_returns_exact_bytes_owner_and_fd_snapshot(
    tmp_path: Path,
) -> None:
    payload = _adoption_payload()
    path = _write(tmp_path / "adoption.json", payload)

    verified = verify_adoption_bundle(path, required_uid=os.geteuid())

    assert verified.owner_telegram_user_id == OWNER_TELEGRAM_USER_ID
    assert verified.payload == payload
    assert verified.snapshot.path == path
    assert verified.snapshot.payload == payload


@pytest.mark.parametrize("case", ["hardlink", "wrong-owner"])
def test_verified_adoption_bundle_rejects_unsafe_host_snapshot(
    tmp_path: Path,
    case: str,
) -> None:
    path = tmp_path / "adoption.json"
    required_uid = os.geteuid()
    if case == "hardlink":
        source = _write(tmp_path / "adoption-source.json", _adoption_payload())
        os.link(source, path)
    else:
        _write(path, _adoption_payload())
        required_uid = os.geteuid() + 1

    with pytest.raises(FbctlError) as raised:
        verify_adoption_bundle(path, required_uid=required_uid)

    assert OIDC_CLIENT_SECRET not in str(raised.value)
    assert OWNER_TELEGRAM_USER_ID not in str(raised.value)


def test_identity_migration_flag_exists_only_on_bootstrap_interfaces() -> None:
    parser = fbctl_main.build_parser()

    bootstrap = parser.parse_args(
        [
            "bootstrap",
            "--source-env",
            "/tmp/source.env",
            "--migrate-existing-bootstrap-identity",
        ]
    )
    remote_publish = parser.parse_args(
        [
            "publish",
            "--host",
            "deploy@example.test",
            "--bundle",
            "/tmp/release.pyz",
            "--source-env-stdin",
            "--bootstrap",
            "--migrate-existing-bootstrap-identity",
        ]
    )
    remote_preflight = parser.parse_args(
        [
            "bootstrap-remote-preflight",
            "--host",
            "deploy@example.test",
            "--bundle",
            "/tmp/preflight.pyz",
            "--source-env-stdin",
            "--project-known-legacy-source",
            "--migrate-existing-bootstrap-identity",
        ]
    )

    assert bootstrap.migrate_existing_bootstrap_identity is True
    assert remote_publish.migrate_existing_bootstrap_identity is True
    assert remote_preflight.migrate_existing_bootstrap_identity is True
    with pytest.raises(SystemExit):
        parser.parse_args(["deploy", "--migrate-existing-bootstrap-identity"])


def test_routine_publish_rejects_identity_migration_before_remote_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoCallsRunner:
        def __init__(self) -> None:
            self.commands: list[tuple[str, ...]] = []

        def run(self, command, **_kwargs):
            self.commands.append(tuple(os.fspath(part) for part in command))
            raise AssertionError("routine publish reached remote transport")

    runner = NoCallsRunner()
    monkeypatch.setattr(
        "fbctl.publish.inspect_bundle",
        lambda _path: SimpleNamespace(
            schema=BUNDLE_SCHEMA,
            release_id="release-1",
            sha256="a" * 64,
        ),
    )

    with pytest.raises(FbctlError):
        publish(
            host="deploy@example.test",
            bundle=tmp_path / "release.pyz",
            root=Path("/opt/fb-agent"),
            source_env_stdin=False,
            docker_config=None,
            bootstrap=False,
            adoption_bundle_remote=None,
            desktop_profile_seed_remote=None,
            enable_scanning=False,
            migrate_existing_bootstrap_identity=True,
            runner=runner,
        )

    assert runner.commands == []


def test_rehearsal_bootstrap_rejects_identity_migration_before_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr("fbctl.controller.sys.version_info", (3, 12))
    root = tmp_path / "fb-agent"
    source = _write(tmp_path / "source.env", _dotenv(_identity_values()))

    class NoCallsRunner:
        def run(self, *_args, **_kwargs):
            raise AssertionError("rehearsal migration reached a runner mutation")

    with pytest.raises(FbctlError):
        bootstrap_host(
            runner=NoCallsRunner(),
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=None,
            docker_config=None,
            rehearsal=True,
            migrate_existing_bootstrap_identity=True,
        )

    assert not root.exists()
