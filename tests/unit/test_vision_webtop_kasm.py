from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEBTOP = ROOT / "deploy" / "vision-webtop"
KASM = ROOT / "deploy" / "kasmvnc-sidecar"
COMPOSE = WEBTOP / "compose.yaml"
INSTALLER = ROOT / "scripts" / "install-vision-webtop.sh"


def test_kasm_is_the_only_digest_pinned_desktop_transport() -> None:
    source = COMPOSE.read_text(encoding="utf-8")

    assert "DESKTOP_WEBTOP_IMAGE:?" in source
    assert "DESKTOP_KASMVNC_IMAGE:?" in source
    assert '"127.0.0.1:8444:8444"' in source
    assert "network_mode: service:webtop" in source
    assert "ipc: shareable" in source
    assert "ipc: service:webtop" in source
    assert "x11-socket:/tmp/.X11-unix" in source
    assert 'DISPLAY_WIDTH: "1366"' in source
    assert 'DISPLAY_HEIGHT: "768"' in source
    assert "guacamole" not in source.lower()
    assert "guacd" not in source.lower()
    assert "5900" not in source
    assert "4822" not in source


def test_webtop_no_longer_installs_or_runs_tigervnc() -> None:
    dockerfile = (WEBTOP / "Dockerfile").read_text(encoding="utf-8")
    assert "tigervnc" not in dockerfile.lower()
    assert "svc-vision-vnc" not in dockerfile
    assert "x11-utils" in dockerfile
    assert not (WEBTOP / "vision-vnc-run").exists()
    assert not (WEBTOP / "bootstrap-guacamole-db.sh").exists()
    assert not (WEBTOP / "guacamole-extension").exists()


def test_kasm_image_and_release_package_are_immutable() -> None:
    dockerfile = (KASM / "Dockerfile").read_text(encoding="utf-8")
    config = (KASM / "kasmvnc.yaml").read_text(encoding="utf-8")
    entrypoint = (KASM / "entrypoint.sh").read_text(encoding="utf-8")

    assert "ubuntu:24.04@sha256:" in dockerfile
    assert "KASMVNC_VERSION=1.4.0" in dockerfile
    assert "12bac6014149c5fdee75f0d403785aaa3e5dd4ea222de73253a5d4181bc9567e" in dockerfile
    assert "sha256sum --check --strict" in dockerfile
    assert "allow_resize: false" in config
    assert "max_frame_rate: 30" in config
    assert "require_ssl: false" in config
    assert 'kasmvncserver "${kasm_display}" -noxstartup' in entrypoint
    assert 'kasmxproxy -a "${source_display}" -v "${kasm_display}" -f 30' in entrypoint
    assert "kasmxproxy -a" in entrypoint
    assert 'kasmxproxy -a "${source_display}" -v "${kasm_display}" -f 30 -r' not in entrypoint


def test_installer_is_quiescent_snapshotting_and_rollback_safe() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert "is_scanning_enabled::text" in source
    assert "lower(status)" in source
    assert '[[ "$state" == "false:0" ]]' in source
    assert "pre-kasm-config.tar.gz" in source
    assert "pre-kasm-baseline.txt" in source
    assert "printf 'display=:1\\n'" in source
    assert 'stable_keys = ("profile_id", "cdp_port")' in source
    assert "compose down --remove-orphans" in source
    assert "restoring compose, /config and browser-agent" in source
    assert "service_is_healthy kasmvnc" in source
    assert "http://127.0.0.1:8444/" in source
    assert "guacamole-postgres" in source  # explicit one-way legacy volume cleanup
    assert "compose build" not in source
