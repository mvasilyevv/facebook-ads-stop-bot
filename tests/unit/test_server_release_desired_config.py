from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _valid_values() -> dict[str, str]:
    return {
        "POSTGRES_DB": "fb_stop_bot",
        "POSTGRES_PASSWORD": "p" * 32,
        "FB_AGENT_BOOTSTRAP_CLUSTER_ID": "c" * 32,
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "ENCRYPTION_KEY_VERIFY": "verified-ciphertext",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_WEBHOOK_SECRET": "w" * 48,
        "ALERTMANAGER_WEBHOOK_SECRET": "m" * 48,
        "TELEGRAM_OIDC_CLIENT_ID": "123456789",
        "TELEGRAM_OIDC_CLIENT_SECRET": "o" * 48,
        "TELEGRAM_OIDC_REDIRECT_URI": ("https://app.adpulse.su/auth/telegram/callback"),
        "API_KEY": "a" * 32,
        "TMA_SESSION_SECRET": "t" * 48,
        "ADSETPRO_POSTBACK_SECRET": "q" * 48,
        "DESKTOP_OWNER_TELEGRAM_USER_ID": "911436108",
        "DESKTOP_PUBLIC_ORIGIN": "https://desktop.adpulse.su",
        "BROWSER_AUTHORITY_CONSUME_URL": (
            "https://app.adpulse.su/api/v1/internal/browser-operations/consume"
        ),
        "BROWSER_MAINTENANCE_CONSUME_URL": (
            "https://app.adpulse.su/api/v1/internal/browser-maintenance/consume"
        ),
        "DESKTOP_KASM_SERVICE_USER": "adpulse-desktop",
        "DESKTOP_KASM_SERVICE_PASSWORD": "k" * 48,
        "REQUIRE_API_KEY": "true",
        "TRUST_PROXY_HEADERS": "true",
        "DEV_TOOLS_ENABLED": "false",
        "LOG_FORMAT": "json",
        "DEPLOYMENT_ENVIRONMENT": "production",
        "FRONTEND_ORIGIN": "https://app.adpulse.su",
        "WEB_APP_URL": "https://app.adpulse.su/tma/",
    }


def _seal_tree(root: Path) -> None:
    for directory, directory_names, file_names in os.walk(root, topdown=False):
        current = Path(directory)
        for name in file_names:
            path = current / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
        for name in directory_names:
            path = current / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
    root.chmod(root.stat().st_mode & ~0o222)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_same_release_retry_after_desired_rotation_fails_without_mutation(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "fb-agent"
    shared = app_root / "shared"
    release = app_root / "releases" / "repeat-release"
    scripts = release / "scripts"
    scripts.mkdir(parents=True)
    shared.mkdir()
    shared.chmod(0o700)

    for name in (
        "browser-control-env.sh",
        "browser-maintenance-lease.sh",
        "server-platform-release.sh",
        "release-state.py",
        "prepare_production_env.py",
    ):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    (scripts / "server-platform-release.sh").chmod(0o755)

    webtop = "registry.example/webtop@sha256:" + "a" * 64
    release_env = release / "release-images.env"
    _write_env(
        release_env,
        {
            "RELEASE_ID": "repeat-release",
            "DESKTOP_WEBTOP_IMAGE": webtop,
        },
    )
    desired = shared / ".env"
    values = _valid_values()
    _write_env(desired, values)
    bootstrap = shared / "bootstrap-secrets.env"
    _write_env(
        bootstrap,
        (
            {
                key: values[key]
                for key in (
                    "FB_AGENT_BOOTSTRAP_CLUSTER_ID",
                    "POSTGRES_PASSWORD",
                    "TMA_SESSION_SECRET",
                    "ADSETPRO_POSTBACK_SECRET",
                    "DESKTOP_KASM_SERVICE_PASSWORD",
                )
            }
        )
        | {
            "BROWSER_MAINTENANCE_CAPABILITY_SECRET": "m" * 64,
            "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE": "a" * 64,
            "BROWSER_OPERATION_CAPABILITY_SECRET_META_API": "o" * 64,
            "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR": "r" * 64,
            "BROWSER_AUTHORITY_CONSUMER_TOKEN": "u" * 64,
        },
    )
    _write_env(shared / "pgbackrest.env", {"BACKUP": "ready"})
    _write_env(shared / "alloy-agent.env", {"ALLOY": "provisioned"})
    _write_env(
        shared / "browser-control.env",
        {
            "BROWSER_MAINTENANCE_CAPABILITY_SECRET": "m" * 64,
            "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE": "a" * 64,
            "BROWSER_OPERATION_CAPABILITY_SECRET_META_API": "o" * 64,
            "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR": "r" * 64,
            "BROWSER_AUTHORITY_CONSUMER_TOKEN": "u" * 64,
        },
    )
    _write_env(
        shared / "browser-maintenance.env",
        {"BROWSER_MAINTENANCE_CAPABILITY_SECRET": "m" * 64},
    )
    _write_env(
        shared / "browser-autopause.env",
        {"BROWSER_OPERATION_CAPABILITY_SECRET": "a" * 64},
    )
    _write_env(
        shared / "browser-meta-api.env",
        {"BROWSER_OPERATION_CAPABILITY_SECRET": "o" * 64},
    )
    _write_env(
        shared / "browser-campaign-creator.env",
        {"BROWSER_OPERATION_CAPABILITY_SECRET": "r" * 64},
    )
    _write_env(
        shared / "browser-authority.env",
        {"BROWSER_AUTHORITY_CONSUMER_TOKEN": "u" * 64},
    )

    production = release / "production.env"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/prepare_production_env.py"),
            "--input",
            str(desired),
            "--bootstrap-secrets",
            str(bootstrap),
            "--output",
            str(production),
            "--desktop-webtop-image",
            webtop,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    production_text = production.read_text(encoding="utf-8")
    assert (
        "BROWSER_AUTHORITY_CONSUME_URL="
        "https://app.adpulse.su/api/v1/internal/browser-operations/consume\n" in production_text
    )
    assert (
        "BROWSER_MAINTENANCE_CONSUME_URL="
        "https://app.adpulse.su/api/v1/internal/browser-maintenance/consume\n" in production_text
    )
    assert "BROWSER_AUTHORITY_CONSUMER_TOKEN=" not in production_text
    assert "BROWSER_OPERATION_CAPABILITY_SECRET" not in production_text
    fingerprint = release / ".fb-agent-effective-config.sha256"
    fingerprint.write_text(f"{_sha256(production)}\n", encoding="ascii")
    fingerprint.chmod(0o600)
    source_manifest = release / ".fb-agent-source-manifest.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/release-state.py"),
            "manifest-write",
            "--release-dir",
            str(release),
            "--manifest",
            str(source_manifest),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    release_checksums = release / ".fb-agent-release"
    release_checksums.write_text(
        "".join(
            f"{_sha256(path)}  {path.name}\n"
            for path in (
                source_manifest,
                fingerprint,
                release_env,
                production,
            )
        ),
        encoding="ascii",
    )
    release_checksums.chmod(0o600)
    _seal_tree(release)

    rotated = values | {"API_KEY": "b" * 32}
    _write_env(desired, rotated)
    desired_before = desired.read_bytes()
    desired_mode_before = desired.stat().st_mode
    journal = shared / "release-transaction.json"
    journal.write_bytes(b"operator-sentinel-journal\n")
    journal.chmod(0o600)
    journal_before = journal.read_bytes()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "calls.log"
    for name in ("docker", "logger", "systemctl", "timeout"):
        stub = bin_dir / name
        stub.write_text(
            f"#!/usr/bin/env bash\nprintf '%s\\n' '{name}' >>\"$CALL_LOG\"\nexit 99\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
    flock = bin_dir / "flock"
    flock.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    flock.chmod(0o755)
    stat_stub = bin_dir / "stat"
    stat_stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'path="${@: -1}"\n'
        'if [[ "$*" == *"%a:%u"* ]]; then\n'
        "  printf '600:%s\\n' \"$(id -u)\"\n"
        "  exit 0\n"
        "fi\n"
        'case "$path" in\n'
        "  */production.env|*/release-images.env|*/.fb-agent-release|"
        "*/.fb-agent-effective-config.sha256|"
        "*/.fb-agent-source-manifest.json) printf '400\\n' ;;\n"
        "  *) printf '600\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stat_stub.chmod(0o755)

    result = subprocess.run(
        [str(scripts / "server-platform-release.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "FB_AGENT_ROOT": str(app_root),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALL_LOG": str(call_log),
        },
    )

    assert result.returncode != 0
    assert (
        "desired effective production config changed after immutable release render"
        in result.stderr
    )
    assert desired.read_bytes() == desired_before
    assert desired.stat().st_mode == desired_mode_before
    assert journal.read_bytes() == journal_before
    assert not call_log.exists() or call_log.read_text(encoding="utf-8") == ""
