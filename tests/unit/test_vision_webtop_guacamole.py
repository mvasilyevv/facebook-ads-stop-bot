from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "vision-webtop" / "compose.yaml"
DOCKERFILE = ROOT / "deploy" / "vision-webtop" / "Dockerfile"
VNC_RUN = ROOT / "deploy" / "vision-webtop" / "vision-vnc-run"
WINDOW_FIT = ROOT / "deploy" / "vision-webtop" / "vision-window-fit-run"
CLIPBOARD_BRIDGE = ROOT / "deploy" / "vision-webtop" / "selkies-clipboard-bridge.js"
INSTALLER = ROOT / "scripts" / "install-vision-webtop.sh"


def test_webtop_has_fixed_shared_geometry_and_private_guacamole_namespace() -> None:
    source = COMPOSE.read_text(encoding="utf-8")

    assert 'DISPLAY_WIDTH: "1366"' in source
    assert 'DISPLAY_HEIGHT: "768"' in source
    assert 'DISPLAY_DPI: "96"' in source
    assert 'SELKIES_IS_MANUAL_RESOLUTION_MODE: "true|locked"' in source
    assert 'SELKIES_MANUAL_WIDTH: "1366"' in source
    assert 'SELKIES_MANUAL_HEIGHT: "768"' in source
    assert 'SELKIES_SCALING_DPI: "96"' in source
    assert "SELKIES_USE_CSS_SCALING" not in source
    assert "guacamole/guacd:1.6.0" in source
    assert "guacamole/guacamole:1.6.0" in source
    assert source.count("network_mode: service:webtop") == 2
    assert 'command: ["-b", "127.0.0.1"]' in source
    assert source.count("condition: service_healthy") == 3
    assert source.count("healthcheck:") == 3
    assert '"127.0.0.1:8090:8080"' in source
    assert '"5900:5900"' not in source
    assert '"4822:4822"' not in source
    assert "JSON_ENABLED" in source
    assert "DESKTOP_GUACAMOLE_JSON_SECRET" in source
    assert "DESKTOP_VNC_PASSWORD" in source


def test_webtop_exports_existing_x11_display_through_tigervnc() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    service = VNC_RUN.read_text(encoding="utf-8")

    assert "tigervnc-scraping-server" in dockerfile
    assert "tigervnc-tools" in dockerfile
    assert "svc-vision-vnc" in dockerfile
    assert "mobile-controls.js" not in dockerfile
    assert "exec X0tigervnc" in service
    assert '-display "$DISPLAY"' in service
    assert "-localhost" in service
    assert "-AlwaysShared" in service
    assert "-PasswordFile" in service
    assert "DESKTOP_VNC_PASSWORD" in service


def test_webtop_patches_safari_clipboard_without_replacing_media_stack() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")
    bridge = CLIPBOARD_BRIDGE.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")

    assert (
        "ubuntu-xfce@sha256:f106549e832cd9b548263d45f81020afd96c4b090a56e1d420e34ce8e4cb36a0"
        in dockerfile
    )
    assert "COPY selkies-clipboard-bridge.js" in dockerfile
    assert "adpulse-clipboard-bridge.js" in dockerfile
    assert "fb-agent/vision-webtop:3.6.8-clipboard2" in compose
    assert 'addEventListener("paste"' in bridge
    assert 'type: "clipboardUpdateFromUI"' in bridge
    assert "__adpulseClipboardReplay" in bridge
    assert 'install -m 0644 \\\n  "$SOURCE_DIR/selkies-clipboard-bridge.js"' in installer


def test_webtop_keeps_isolated_vision_window_maximizer() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    watcher = WINDOW_FIT.read_text(encoding="utf-8")

    assert "wmctrl" in dockerfile
    assert "svc-vision-window-fit" in dockerfile
    assert "/config/.local/share/Vision/profiles/" in watcher
    assert "_NET_WM_STATE_MAXIMIZED_VERT" in watcher
    assert "add,maximized_vert,maximized_horz" in watcher


def test_installer_uses_release_env_and_checks_all_desktop_services() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")

    assert 'readonly COMPOSE_ENV_FILE="$PROJECT_DIR/.env"' in installer
    assert '--env-file "$COMPOSE_ENV_FILE"' in installer
    assert 'install -m 0755 "$SOURCE_DIR/vision-vnc-run"' in installer
    assert 'rm -f -- "$TARGET_DIR/mobile-controls.js"' in installer
    assert "compose config --quiet" in installer
    assert "service_is_healthy webtop" in installer
    assert "service_is_healthy guacd" in installer
    assert "service_is_healthy guacamole" in installer
    assert "http://127.0.0.1:8090/guacamole/" in installer
    assert "pgrep -x X0tigervnc" in installer
    assert "/dev/tcp/127.0.0.1/5900" not in installer
    assert "/dev/tcp/127.0.0.1/4822" not in installer
    assert 'docker rm -f "$BROWSER_AGENT_CONTAINER"' in installer
    assert "ensure_cdp_ready" in installer
