from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WEBTOP = ROOT / "deploy" / "vision-webtop"
COMPOSE = ROOT / "deploy/compose/docker-compose.desktop-agent.yml"


def test_desktop_is_one_digest_pinned_runtime_plane() -> None:
    source = COMPOSE.read_text(encoding="utf-8")
    document = yaml.safe_load(source)

    assert tuple(document["services"]) == (
        "vision-webtop",
        "browser-agent",
        "rustdesk-id",
        "rustdesk-relay",
    )
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
    # Браузер на столе приходит тем же путём, что KasmVNC и Vision: официальный
    # артефакт по фиксированной версии со сверкой контрольной суммы. apt-пакет
    # firefox в Ubuntu 24.04 — заглушка над snap и внутри контейнера не работает.
    assert "FIREFOX_VERSION=140.13.0esr" in dockerfile
    assert "866d7e5f94abe93132e02a0db72da32b6e133905fcbea6afa417a96d496021da" in dockerfile
    assert "ftp.mozilla.org/pub/firefox/releases" in dockerfile
    assert '"${FIREFOX_SHA256}  /tmp/firefox.tar.xz" | sha256sum --check --strict' in dockerfile
    assert "Firefox 140.13.0esr" in notices


def test_desktop_resizes_but_the_vision_window_stays_fixed() -> None:
    """Стол тянется за окном оператора, окно кабинета — нет.

    Раньше и то и другое было прибито к 1366x768: на большом мониторе рабочая
    область показывалась растянутой картинкой. Развязываем, но окно Vision
    оставляем фиксированным — размер видимой области входит в отпечаток
    кабинета, и его скачки от сессии к сессии профилю не на пользу.
    """
    config = (WEBTOP / "kasmvnc.yaml").read_text(encoding="utf-8")
    window_fit = (WEBTOP / "vision-window-fit.sh").read_text(encoding="utf-8")

    assert "allow_resize: true" in config
    assert "readonly vision_width=1366" in window_fit
    assert "readonly vision_height=768" in window_fit
    # Точный размер задаётся только развёрнутому окну.
    assert "remove,maximized_vert,maximized_horz" in window_fit
    assert '-e "0,${offset_x},${offset_y},${vision_width},${vision_height}"' in window_fit
    # На экране меньше окна разворачиваем во весь стол: иначе часть окна
    # недостижима, а это случай телефона.
    assert "((screen_width >= vision_width))" in window_fit
    assert "add,maximized_vert,maximized_horz" in window_fit


def test_desktop_is_a_full_environment_not_a_single_app_kiosk() -> None:
    """Стол обязан оставаться полноценным окружением.

    До 18.07 образ собирался на готовом linuxserver/webtop и был полным. При
    переходе на KasmVNC его пересобрали с нуля и оставили пять пакетов —
    владелец увидел это как «урезанный стол» и попросил вернуть полный.
    Список поимённый: метапакет при --no-install-recommends тянет только
    Depends, и состав окружения перестаёт быть нашим решением.
    """
    dockerfile = (WEBTOP / "Dockerfile").read_text(encoding="utf-8")

    for package in (
        "xfce4-appfinder",
        "xfce4-settings",
        "xfce4-notifyd",
        "xfce4-clipman-plugin",
        "xfce4-screenshooter",
        "xfce4-taskmanager",
        "thunar-archive-plugin",
        "thunar-volman",
        "atril",
        "engrampa",
        "galculator",
        "mousepad",
        "ristretto",
        "gvfs",
    ):
        assert package in dockerfile, f"полный стол потерял {package}"
    # Блокировка экрана внутри контейнера оставила бы оператора перед паролем,
    # которого он не знает; управление питанием там бессмысленно.
    assert "xfce4-screensaver" not in dockerfile
    assert "xfce4-power-manager" not in dockerfile


def test_desktop_ships_a_plain_browser_the_operator_can_find() -> None:
    """Браузер должен быть виден на столе, а не только в меню.

    Стол собран минимальным намеренно, но без обычного браузера оператор не мог
    открыть облачные панели — ни Vision, ни трекер, ни кабинет.
    """
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    launcher = (WEBTOP / "firefox.desktop").read_text(encoding="utf-8")
    dockerfile = (WEBTOP / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY firefox.desktop /usr/share/applications/firefox.desktop" in dockerfile
    assert "ln -s /opt/firefox/firefox /usr/local/bin/firefox" in dockerfile
    assert '"${config_home}/Desktop"' in entrypoint
    assert '"${config_home}/Desktop/firefox.desktop"' in entrypoint
    assert "Exec=/opt/firefox/firefox %u" in launcher
    # Предупреждение в подписи: кабинеты открываются только через профиль Vision.
    assert "только через профиль Vision" in launcher


def test_single_runtime_owns_display_one_and_health() -> None:
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    healthcheck = (WEBTOP / "healthcheck.sh").read_text(encoding="utf-8")
    config = (WEBTOP / "kasmvnc.yaml").read_text(encoding="utf-8")

    assert "readonly display=:1" in entrypoint
    assert 'kasmvncserver "${display}" -noxstartup -geometry 1366x768 -depth 24' in entrypoint
    assert 'DISPLAY="${display}"' in entrypoint
    assert "/usr/bin/Vision" in entrypoint
    assert "pgrep -x Vision" in healthcheck
    # Проверка здоровья не привязана к конкретному размеру: стол тянется за
    # окном оператора, и требование ровно 1366x768 объявило бы здоровый десктоп
    # больным при первом же изменении размера.
    assert "dimensions:[[:space:]]+[1-9][0-9]{2,4}x[1-9][0-9]{2,4}" in healthcheck
    assert "dimensions:[[:space:]]+1366x768" not in healthcheck
    assert "allow_resize: true" in config
    assert "max_frame_rate: 30" in config
    assert "require_ssl: false" in config
    assert "kasmxproxy" not in entrypoint
    assert ":10" not in entrypoint
    assert "X10" not in entrypoint


def _healthcheck_with_stubs(tmp_path: Path, *, vision_running: bool = True) -> int:
    """Выполнить настоящий healthcheck.sh, подменив внешние команды заглушками.

    xdpyinfo печатает строку с разрешением, а следом ещё много вывода: так
    проверяется, что скрипт дочитывает его до конца. Конвейер `| grep -q`
    здесь обрывает чтение, xdpyinfo ловит SIGPIPE и pipefail возвращает 141 —
    здоровый рабочий стол становится unhealthy.
    """
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "xdpyinfo").write_text(
        '#!/bin/sh\necho "  dimensions:    1366x768 pixels"\n'
        'i=0\nwhile [ "$i" -lt 4000 ]; do echo "  filler line $i"; i=$((i + 1)); done\n',
        encoding="utf-8",
    )
    (stubs / "pgrep").write_text(
        "#!/bin/sh\n"
        + ("exit 0\n" if vision_running else 'case "$*" in *Vision*) exit 1;; esac\nexit 0\n'),
        encoding="utf-8",
    )
    (stubs / "curl").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for stub in stubs.iterdir():
        stub.chmod(0o755)

    return subprocess.run(
        ["bash", str(WEBTOP / "healthcheck.sh")],
        env={
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "DESKTOP_KASM_SERVICE_USER": "service",
            "DESKTOP_KASM_SERVICE_PASSWORD": "x" * 16,
        },
        capture_output=True,
        check=False,
    ).returncode


def test_healthcheck_passes_when_desktop_is_actually_up(tmp_path: Path) -> None:
    assert _healthcheck_with_stubs(tmp_path) == 0


def test_healthcheck_fails_when_vision_is_gone(tmp_path: Path) -> None:
    assert _healthcheck_with_stubs(tmp_path, vision_running=False) != 0


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
    assert 'UI.forceSetting("reconnect_delay", 250)' in client
    assert 'UI.forceSetting("translate_shortcuts", true)' in client
    # Плановый обрыв прокси не должен выглядеть аварией, но настоящая
    # неудача обязана дойти до оператора со второй попытки.
    assert "installQuietReconnect()" in client
    assert 'if (failures === 1) return original("Переподключаемся…", "warn", time)' in client
    assert "return original(text, statusType, time)" in client
    # Рабочий стол вписывается в окно: 1366x768 не меняются, и при 100%
    # часть экрана уезжала за край.
    assert 'let screenMode = "scale"' in client
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
    assert "kasm_password_file: /config/.kasmpasswd" in config
    assert "readonly password_file=/config/.kasmpasswd" in entrypoint
    # Конфиг теперь действительно применяется, поэтому его значения обязаны
    # быть валидными: log_dest: stdout эта сборка отвергает при разборе.
    assert "log_dest: logfile" in config
    assert "log_dest: stdout" not in config
    # Сервер ищет пользователей только в ${HOME}/.kasmpasswd, поэтому ссылка
    # обязательна; сам пароль остаётся в /run и на диск профиля не попадает.
    # Управляемый профиль не принимает симлинки, поэтому файл паролей — обычный.
    assert "ln -sfn" not in entrypoint
    # Ключ snakeoil закрыт группой ssl-cert: без членства сервер сообщает об
    # отказе в доступе как об отсутствии файла и не стартует.
    dockerfile = (WEBTOP / "Dockerfile").read_text(encoding="utf-8")
    assert "usermod --append --groups ssl-cert vision" in dockerfile


def test_rustdesk_is_a_pinned_optional_second_channel() -> None:
    """Нативный канал к столу: буфер обмена на телефоне браузером не решается.

    В Safari на iPhone чтение буфера требует свежего жеста, а WebKit считает
    жест протухшим после любого await — VNC-клиенту, который ходит на сервер,
    его не хватает. Поэтому рядом с браузером живёт нативный протокол.
    """
    dockerfile = (WEBTOP / "Dockerfile").read_text(encoding="utf-8")
    notices = (WEBTOP / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    # Версия не ниже 1.4.6: в ней закрыты три уязвимости высокой критичности,
    # задевавшие всё до 1.4.5 включительно.
    assert "RUSTDESK_VERSION=1.4.6" in dockerfile
    assert "0da46d7a7b252282ded5323f74319a10c1fa7271001d3b297b3def415c8c8f04" in dockerfile
    assert '"${RUSTDESK_SHA256}  /tmp/rustdesk.deb" | sha256sum --check --strict' in dockerfile
    assert "RustDesk 1.4.6" in notices


def test_rustdesk_stays_closed_until_the_owner_sets_a_password() -> None:
    """Открытый наружу порт без пароля — не то, что должно появиться само."""
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "DESKTOP_RUSTDESK_PASSWORD" in entrypoint
    # Пустой пароль означает «канал не нужен», а не «канал без пароля».
    assert "rustdesk_password=${DESKTOP_RUSTDESK_PASSWORD:-}" in entrypoint
    assert "((${#rustdesk_password} < 16))" in entrypoint
    # Наружу смотрит только брокер, и то по адресу, который задаёт владелец.
    assert "${DESKTOP_RUSTDESK_BIND:-127.0.0.1}" in compose
    assert "DESKTOP_RUSTDESK_SERVER" in entrypoint
    assert "DESKTOP_RUSTDESK_KEY" in entrypoint


def test_rustdesk_uses_the_live_display_and_never_takes_the_desktop_down() -> None:
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")

    # Стол у нас настоящий, с framebuffer: headless-режим RustDesk с его
    # известными болячками нам не нужен и не включается.
    assert "allow-linux-headless" not in entrypoint
    assert "custom-rendezvous-server" in entrypoint
    # Второй канал — дополнение. Его падение не должно ронять рабочий стол
    # оператора вместе с открытым кабинетом.
    assert "rustdesk_supervisor" in entrypoint


def test_rustdesk_never_falls_back_to_the_public_broker() -> None:
    """Чужой брокер для машины с открытым кабинетом — не запасной вариант.

    По умолчанию RustDesk регистрируется на rs-*.rustdesk.com. Проверено на
    живом контейнере: в логах клиента появляется публичный сервер. Поэтому
    канал поднимается только вместе со своим брокером, а не «как получится».
    """
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")
    document = yaml.safe_load(compose)

    code = "\n".join(line for line in entrypoint.splitlines() if not line.lstrip().startswith("#"))
    assert "rustdesk.com" not in code
    # Без адреса своего брокера и его ключа канал не поднимается вовсе.
    assert "require_env DESKTOP_RUSTDESK_SERVER" in entrypoint
    assert "require_env DESKTOP_RUSTDESK_KEY" in entrypoint

    broker = document["services"]["rustdesk-id"]
    relay = document["services"]["rustdesk-relay"]
    assert "21116" in " ".join(map(str, broker["ports"]))
    assert "21117" in " ".join(map(str, relay["ports"]))
    # Ключи брокера переживают пересоздание контейнера, иначе клиенты
    # перестанут ему верить после каждого деплоя.
    assert broker["volumes"]
