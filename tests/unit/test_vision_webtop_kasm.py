from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WEBTOP = ROOT / "deploy" / "vision-webtop"
COMPOSE = ROOT / "deploy/compose/docker-compose.desktop-agent.yml"


def test_desktop_is_one_digest_pinned_runtime_plane() -> None:
    source = COMPOSE.read_text(encoding="utf-8")
    document = yaml.safe_load(source)

    assert tuple(document["services"]) == ("vision-webtop", "browser-agent")
    assert "DESKTOP_WEBTOP_IMAGE:?" in source
    assert "DESKTOP_HTTPS_PORT:?" in source
    assert "VISION_CONFIG_DIR:?" in source
    assert 'network_mode: "service:vision-webtop"' in source
    assert "ipc:" not in source
    assert "/tmp/.X11-unix" not in source
    assert "guacamole" not in source.lower()
    assert "guacd" not in source.lower()
    assert "5900" not in source
    assert "4822" not in source


def test_immutable_image_pins_kasmvnc_vision_and_first_party_client() -> None:
    dockerfile = (WEBTOP / "Dockerfile").read_text(encoding="utf-8")
    notices = (WEBTOP / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "ubuntu:24.04@sha256:" in dockerfile
    assert "node:22-slim@sha256:" in dockerfile
    assert "KASMVNC_VERSION=1.5.0" in dockerfile
    assert "KASMVNC_SOURCE_COMMIT=17265facc40ab50db5740cdf0d12c61173edafc9" in dockerfile
    assert "KASM_NOVNC_SOURCE_COMMIT=475ecfa5356579ef222983c7ce4619a7576a3bce" in dockerfile
    assert "f599fe02e2175b9817b6165f74a5d2bebdc73118dde9181ba3410963bed7ae1e" in dockerfile
    assert "sha256sum --check --strict" in dockerfile
    assert 'test "$(getent passwd 1000 | cut -d: -f1)" = "ubuntu"' in dockerfile
    assert 'test "$(getent group 1000 | cut -d: -f1)" = "ubuntu"' in dockerfile
    assert "usermod --login vision --home /config --shell /bin/bash ubuntu" in dockerfile
    assert "groupmod --new-name vision ubuntu" in dockerfile
    assert 'test "$(id -u vision)" = "1000"' in dockerfile
    assert 'test "$(id -g vision)" = "1000"' in dockerfile
    assert "groupadd --gid 1000 vision" not in dockerfile
    assert "useradd --uid 1000" not in dockerfile
    assert "COPY --from=kasm-client-builder" in dockerfile
    assert "KasmVNC 1.5.0" in notices
    assert "17265facc40ab50db5740cdf0d12c61173edafc9" in notices


def test_single_runtime_owns_display_one_and_health() -> None:
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    healthcheck = (WEBTOP / "healthcheck.sh").read_text(encoding="utf-8")
    config = (WEBTOP / "kasmvnc.yaml").read_text(encoding="utf-8")

    assert "readonly display=:1" in entrypoint
    assert 'kasmvncserver "${display}" -noxstartup -geometry 1366x768 -depth 24' in entrypoint
    assert 'DISPLAY="${display}"' in entrypoint
    assert "/usr/bin/Vision" in entrypoint
    assert "pgrep -x Vision" in healthcheck
    assert "1366x768" in healthcheck
    assert "allow_resize: false" in config
    assert "max_frame_rate: 30" in config
    assert "require_ssl: false" in config
    assert "kasmxproxy" not in entrypoint
    assert ":10" not in entrypoint
    assert "X10" not in entrypoint


def test_first_party_client_is_profile_bound_and_fail_closed() -> None:
    client = (WEBTOP / "kasm-client/fb-agent-client.js").read_text(encoding="utf-8")

    assert 'fetch("/desktop-auth/profile"' in client
    assert 'credentials: "same-origin"' in client
    assert 'cache: "no-store"' in client
    assert 'if (!response.ok) throw new Error("desktop_session_required")' in client
    assert 'payload?.presentation !== "desktop"' in client
    assert 'payload?.presentation !== "mobile"' in client
    assert "catch {\n    failClosed();\n  }" in client
    assert 'dataset.fbDesktopState = "denied"' in client


def test_first_party_client_keeps_local_scaling_clipboard_and_reconnect_contract() -> None:
    client = (WEBTOP / "kasm-client/fb-agent-client.js").read_text(encoding="utf-8")
    patcher = (WEBTOP / "kasm-client/apply-patch.mjs").read_text(encoding="utf-8")
    styles = (WEBTOP / "kasm-client/fb-agent-client.css").read_text(encoding="utf-8")
    dockerfile = (WEBTOP / "Dockerfile").read_text(encoding="utf-8")

    assert "const MAX_CLIPBOARD_BYTES = 256 * 1024" in client
    assert '"text/plain"' in client
    assert 'UI.forceSetting("reconnect", true)' in client
    assert 'UI.forceSetting("reconnect_delay", 1000)' in client
    assert 'UI.forceSetting("translate_shortcuts", true)' in client
    assert "Literal Linux" in client
    assert "get localScale()" in patcher
    assert "set localScale(scale)" in patcher
    assert "this._resizeSession = false" in patcher
    assert "this._scaleViewport = false" in patcher
    assert "core/rfb.js:local-scale" in patcher
    assert "dist/" not in patcher
    assert "min-height: 44px" in styles
    assert "env(safe-area-inset-bottom" in styles
    assert "env(safe-area-inset-left" in styles
    assert "env(safe-area-inset-right" in styles
    patch_index = dockerfile.index("node /opt/fb-agent-kasm/apply-patch.mjs /src/kasmweb")
    build_index = dockerfile.index("npm run build", patch_index)
    assert patch_index < build_index


def test_legacy_split_desktop_runtime_cannot_reenter_release_contract() -> None:
    assert not (ROOT / "deploy" / "kasmvnc-sidecar").exists()
    for retired_file in (
        "disable-server-capslock",
        "disable-server-capslock.desktop",
        "vision-service-run",
        "vision-window-fit-run",
    ):
        assert not (WEBTOP / retired_file).exists()

    guarded = (
        ROOT / ".env.example",
        ROOT / ".github/workflows/publish-images.yml",
        ROOT / ".github/workflows/release.yml",
        ROOT / "deploy/compose/docker-compose.desktop-agent.yml",
        ROOT / "scripts/fbctl",
        ROOT / "fbctl/controller.py",
        ROOT / "fbctl/config.py",
    )
    retired_tokens = (
        "DESKTOP_KASMVNC_IMAGE",
        "kasmvnc-sidecar",
        "kasmxproxy",
        "DISPLAY=:10",
        "DISPLAY=10",
        "X10",
    )
    for path in guarded:
        source = path.read_text(encoding="utf-8")
        for token in retired_tokens:
            assert token not in source, (path.relative_to(ROOT), token)


def test_entrypoint_installs_managed_config_into_the_mounted_profile() -> None:
    """Пустой профиль не должен ронять десктоп.

    Конфиг из образа лежит в /etc/kasmvnc, а профиль монтируется поверх HOME.
    На чистом профиле сервер создаёт там свой дефолт, тот перекрывает
    системный конфиг, теряет путь к файлу паролей и падает с «No users
    configured» — что и случилось на первом боевом bootstrap.
    """
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    config = (WEBTOP / "kasmvnc.yaml").read_text(encoding="utf-8")

    assert "/etc/kasmvnc/kasmvnc.yaml" in entrypoint
    assert '"${config_home}/.vnc/kasmvnc.yaml"' in entrypoint
    # Путь к файлу паролей задаёт именно управляемый конфиг.
    assert "kasm_password_file: /run/kasmvnc/.kasmpasswd" in config
    assert "readonly password_file=/run/kasmvnc/.kasmpasswd" in entrypoint
