"""Контракт рабочего стола после отказа от веб-канала.

KasmVNC был не веб-мордой, а X-сервером стола. Его место занял Xvfb, а
единственный путь к столу — нативный RustDesk через собственный брокер.
Эти тесты держат контракт: без канала стол не стартует, публичный брокер
недостижим по построению, а следы веб-канала не могут вернуться незаметно.
"""

from __future__ import annotations

import json
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
    assert "VISION_CONFIG_DIR:?" in source
    assert 'network_mode: "service:vision-webtop"' in source
    # Веб-канал не возвращается: у стола нет опубликованных портов, кроме
    # loopback-gRPC browser-agent'а; наружу смотрит только брокер.
    webtop_ports = document["services"]["vision-webtop"]["ports"]
    assert webtop_ports == ["127.0.0.1:${BROWSER_GRPC_HOST_PORT:?set BROWSER_GRPC_HOST_PORT}:50051"]
    assert "8444" not in source
    assert "DESKTOP_HTTPS_PORT" not in source
    assert "kasm" not in source.lower()
    # ID канала стол публикует в readiness-каталог — оттуда его читает API;
    # ключ брокера стол читает из каталога брокера.
    assert "${DESKTOP_READINESS_DIR:?set DESKTOP_READINESS_DIR}:/run/desktop-readiness" in source
    assert (
        "${DESKTOP_RUSTDESK_DATA_DIR:?set DESKTOP_RUSTDESK_DATA_DIR}:/run/rustdesk-broker:ro"
        in source
    )
    assert "ipc:" not in source
    assert "/tmp/.X11-unix" not in source
    assert "guacamole" not in source.lower()
    assert "5900" not in source


def test_immutable_image_pins_every_supply_chain_input() -> None:
    dockerfile = (WEBTOP / "Dockerfile").read_text(encoding="utf-8")
    notices = (WEBTOP / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "ubuntu:24.04@sha256:" in dockerfile
    assert "sha256sum --check --strict" in dockerfile
    assert 'test "$(getent passwd 1000 | cut -d: -f1)" = "ubuntu"' in dockerfile
    assert "usermod --login vision --home /config --shell /bin/bash ubuntu" in dockerfile
    assert 'test "$(id -u vision)" = "1000"' in dockerfile
    # Браузер и Vision приходят фиксированными артефактами со сверкой суммы.
    assert "FIREFOX_VERSION=140.13.0esr" in dockerfile
    assert "866d7e5f94abe93132e02a0db72da32b6e133905fcbea6afa417a96d496021da" in dockerfile
    assert "Firefox 140.13.0esr" in notices
    assert "VISION_VERSION=3.6.8" in dockerfile
    # Канал: не ниже 1.4.6 — в ней закрыты три уязвимости высокой критичности.
    assert "RUSTDESK_VERSION=1.4.6" in dockerfile
    assert "0da46d7a7b252282ded5323f74319a10c1fa7271001d3b297b3def415c8c8f04" in dockerfile
    assert '"${RUSTDESK_SHA256}  /tmp/rustdesk.deb" | sha256sum --check --strict' in dockerfile
    assert "RustDesk 1.4.6" in notices
    # X-сервер — Xvfb; KasmVNC и его noVNC-клиент не возвращаются.
    assert "xvfb" in dockerfile
    assert "kasm" not in dockerfile.lower()
    assert "FROM node" not in dockerfile


def test_desktop_serves_a_fixed_display_the_native_client_scales() -> None:
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    window_fit = (WEBTOP / "vision-window-fit.sh").read_text(encoding="utf-8")

    assert "readonly display=:1" in entrypoint
    # Размер стола фиксирован: подстраивать его больше не под что — нативный
    # клиент масштабирует картинку сам.
    assert 'Xvfb "${display}" -screen 0 1920x1080x24 -nolisten tcp' in entrypoint
    assert "kasmvncserver" not in entrypoint
    # Окно кабинета остаётся фиксированным: размер видимой области — часть
    # отпечатка профиля, и скакать от сессии к сессии он не должен.
    assert "readonly vision_width=1366" in window_fit
    assert "readonly vision_height=768" in window_fit
    assert '-e "0,${offset_x},${offset_y},${vision_width},${vision_height}"' in window_fit


def test_desktop_is_a_full_environment_not_a_single_app_kiosk() -> None:
    """Стол обязан оставаться полноценным окружением.

    При прошлой пересборке с нуля от окружения осталось пять пакетов —
    владелец увидел «урезанный стол». Список поимённый: метапакет при
    --no-install-recommends тянет только Depends, и состав окружения
    перестал бы быть нашим решением.
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
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    launcher = (WEBTOP / "firefox.desktop").read_text(encoding="utf-8")
    dockerfile = (WEBTOP / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY firefox.desktop /usr/share/applications/firefox.desktop" in dockerfile
    assert "ln -s /opt/firefox/firefox /usr/local/bin/firefox" in dockerfile
    assert '"${config_home}/Desktop/firefox.desktop"' in entrypoint
    assert "Exec=/opt/firefox/firefox %u" in launcher
    assert "только через профиль Vision" in launcher


def _healthcheck_with_stubs(
    tmp_path: Path,
    *,
    vision_running: bool = True,
    rustdesk_running: bool = True,
) -> int:
    """Выполнить настоящий healthcheck.sh, подменив внешние команды заглушками.

    xdpyinfo печатает строку с разрешением и много вывода следом: так
    проверяется, что скрипт дочитывает его до конца, а не ловит SIGPIPE
    в конвейере с grep -q (healthy стол возвращал 141 и считался больным).
    """
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "xdpyinfo").write_text(
        '#!/bin/sh\necho "  dimensions:    1920x1080 pixels"\n'
        'i=0\nwhile [ "$i" -lt 4000 ]; do echo "  filler line $i"; i=$((i + 1)); done\n',
        encoding="utf-8",
    )
    checks = []
    if not vision_running:
        checks.append('case "$*" in *Vision*) exit 1;; esac')
    if not rustdesk_running:
        checks.append('case "$*" in *rustdesk*) exit 1;; esac')
    (stubs / "pgrep").write_text(
        "#!/bin/sh\n" + "\n".join(checks) + ("\n" if checks else "") + "exit 0\n",
        encoding="utf-8",
    )
    for stub in stubs.iterdir():
        stub.chmod(0o755)

    return subprocess.run(
        ["bash", str(WEBTOP / "healthcheck.sh")],
        env={"PATH": f"{stubs}:{os.environ['PATH']}"},
        capture_output=True,
        check=False,
    ).returncode


def test_healthcheck_passes_when_desktop_is_actually_up(tmp_path: Path) -> None:
    assert _healthcheck_with_stubs(tmp_path) == 0


def test_healthcheck_fails_when_vision_is_gone(tmp_path: Path) -> None:
    assert _healthcheck_with_stubs(tmp_path, vision_running=False) != 0


def test_healthcheck_fails_when_the_only_channel_is_gone(tmp_path: Path) -> None:
    """Канал единственный: без него машина недостижима, и это болезнь."""
    assert _healthcheck_with_stubs(tmp_path, rustdesk_running=False) != 0


def test_desktop_refuses_to_start_without_the_channel() -> None:
    """Пустой пароль раньше означал «канал не нужен» — веб-канал был запасным.

    Теперь запасного нет: стол без канала — недостижимая машина, и отказ на
    старте не даёт ей стать такой молча. SSH при этом остаётся всегда.
    """
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")

    assert "require_env DESKTOP_RUSTDESK_PASSWORD" in entrypoint
    assert "require_env DESKTOP_RUSTDESK_SERVER" in entrypoint
    assert "((${#DESKTOP_RUSTDESK_PASSWORD} < 16))" in entrypoint
    # Ключ брокера приходит файлом из его каталога; без ключа канал не жив,
    # и стол честно отказывается стартовать, а не ждёт молча вечность.
    assert "/run/rustdesk-broker/id_ed25519.pub" in entrypoint
    assert "RustDesk broker public key did not appear" in entrypoint


def test_channel_never_reaches_the_public_broker() -> None:
    """Чужой брокер для машины с открытым кабинетом — не запасной вариант.

    По умолчанию RustDesk регистрируется на rs-*.rustdesk.com — проверено на
    живом контейнере. Конфиг раскладывается файлом: `rustdesk --option` до
    файла не доезжает, а запущенный клиент перезаписывает его своим
    состоянием.
    """
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")

    code = "\n".join(line for line in entrypoint.splitlines() if not line.lstrip().startswith("#"))
    assert "rustdesk.com" not in code
    assert "custom-rendezvous-server = '${rustdesk_id_server}'" in entrypoint
    assert "relay-server = '${rustdesk_relay_server}'" in entrypoint
    assert "key = '${rustdesk_key}'" in entrypoint
    assert "verification-method = 'use-permanent-password'" in entrypoint
    # Стол настоящий, с framebuffer: headless-режим RustDesk с его известными
    # болячками не включается.
    assert "allow-linux-headless" not in entrypoint


def test_desktop_reaches_the_broker_inside_its_own_network() -> None:
    """Стол и оператор смотрят на один брокер с разных сторон.

    Обратный путь из контейнера на published-адрес хоста закрыт: стол молча
    не регистрировался, ID был, а подключиться было некуда. Внутри compose
    ID-сервер доступен по имени сервиса.

    С реле так нельзя: стол сообщает его адрес брокеру при регистрации, а
    брокер отдаёт ту же строку внешнему клиенту. Внутреннее имя означало бы
    адрес, которого снаружи не существует, и сессия рвалась бы сразу после
    успешного рукопожатия. Поэтому реле адресуется именем канала — снаружи
    оно ведёт на хост, внутри его же compose вешает алиасом на контейнер реле.
    """
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    environment = document["services"]["vision-webtop"]["environment"]

    assert environment["DESKTOP_RUSTDESK_ID_SERVER"] == "rustdesk-id"
    assert "DESKTOP_RUSTDESK_SERVER" in environment["DESKTOP_RUSTDESK_RELAY_SERVER"]
    assert "rustdesk-relay" not in environment["DESKTOP_RUSTDESK_RELAY_SERVER"]

    relay_aliases = document["services"]["rustdesk-relay"]["networks"]["platform"]["aliases"]
    assert any("DESKTOP_RUSTDESK_SERVER" in alias for alias in relay_aliases)
    # Алиас имени канала висит только на реле: два контейнера под одним именем
    # Docker DNS раздавал бы по очереди, и часть обращений уходила бы в сервис
    # без нужного порта.
    id_aliases = document["services"]["rustdesk-id"]["networks"]["platform"]["aliases"]
    assert not any("DESKTOP_RUSTDESK_SERVER" in alias for alias in id_aliases)

    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    # Оператору публикуется публичный адрес, а не внутреннее имя сервиса.
    assert '"${DESKTOP_RUSTDESK_SERVER}" "${rustdesk_key}"' in entrypoint


def test_channel_failure_never_takes_the_desktop_down() -> None:
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")

    assert "rustdesk_supervisor" in entrypoint
    # Падение канала поднимается на месте, а не роняет стол с открытым
    # кабинетом; Xvfb и Vision живут в основном цикле надзора.
    assert "X display exited unexpectedly" in entrypoint
    assert "Vision exited unexpectedly" in entrypoint


def test_device_id_is_published_for_the_operator_ui() -> None:
    """ID выдаёт брокер и заранее он неизвестен: OSS-брокер не поддерживает
    кастомные ID (`--set-id` → server_not_support, проверено на живом
    контейнере). Стол публикует ID в readiness-каталог, оттуда его читает
    операторский API — иначе первый вход потребовал бы ssh на хост.
    """
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")

    assert "rustdesk --get-id" in entrypoint
    assert "/run/desktop-readiness/rustdesk.json" in entrypoint
    # Публикация атомарна: читатель не должен увидеть полфайла.
    assert "mv -f /run/desktop-readiness/rustdesk.json.tmp" in entrypoint
    # Ключ и адрес публикуются сразу, ID — как только его выдаст брокер.
    assert 'publish_channel_info ""' in entrypoint


def _run_publish_channel_info(tmp_path: Path, device_id: str) -> str:
    """Исполняет функцию публикации прямо из entrypoint.

    Проверять публикацию по наличию строк бессмысленно: файл читает не человек,
    а операторский API, и невалидный JSON он честно превращает в «канала нет».
    Поэтому запускаем настоящий код и смотрим на результат.
    """
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    start = entrypoint.index("  publish_channel_info() {")
    end = entrypoint.index("\n  }\n", start) + len("\n  }\n")
    function = entrypoint[start:end].replace("/run/desktop-readiness", str(tmp_path))

    script = (
        "set -Eeuo pipefail\n"
        "DESKTOP_RUSTDESK_SERVER=100.73.162.127\n"
        "rustdesk_key='QJztruGKKjvEcX9XBLMixf21wieLGYABEaWby97JP5s='\n"
        f"{function}\n"
        f'publish_channel_info "{device_id}"\n'
    )
    subprocess.run(["bash", "-c", script], check=True, capture_output=True)
    return (tmp_path / "rustdesk.json").read_text(encoding="utf-8")


def test_channel_info_is_valid_json_before_the_broker_issues_an_id(tmp_path: Path) -> None:
    """Адрес и ключ публикуются раньше ID — оператор настраивает клиент, пока
    стол поднимается. Если ранняя публикация ломает JSON, API отдаёт пустоту, и
    смысл ранней публикации теряется целиком.
    """
    published = json.loads(_run_publish_channel_info(tmp_path, ""))

    assert published["device_id"] is None
    assert published["server"] == "100.73.162.127"
    assert published["key"] == "QJztruGKKjvEcX9XBLMixf21wieLGYABEaWby97JP5s="


def test_channel_info_carries_the_device_id_once_it_is_known(tmp_path: Path) -> None:
    published = json.loads(_run_publish_channel_info(tmp_path, "253474910"))

    assert published["device_id"] == "253474910"
    assert published["server"] == "100.73.162.127"


def test_legacy_desktop_runtimes_cannot_reenter_release_contract() -> None:
    assert not (ROOT / "deploy" / "kasmvnc-sidecar").exists()
    assert not (WEBTOP / "kasmvnc.yaml").exists()
    assert not (WEBTOP / "kasm-client").exists()

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
        "DESKTOP_KASM_SERVICE",
        "DESKTOP_HTTPS_PORT",
        "DISPLAY=:10",
        "X10",
    )
    for path in guarded:
        source = path.read_text(encoding="utf-8")
        # Единственное законное место ретированного имени — сам механизм
        # ретирования: RETIRED_SOURCE_KEYS в fbctl отбрасывает эти ключи из
        # старых секретов, и без имён он бы не работал.
        if path.name == "config.py":
            head, _, tail = source.partition("RETIRED_SOURCE_KEYS = frozenset(")
            tail = tail.partition(")")[2]
            source = head + tail
        for token in retired_tokens:
            assert token not in source, (path.relative_to(ROOT), token)


def test_third_party_notices_describe_the_shipped_image() -> None:
    """Notices — документ соответствия, а не история образа.

    Снятый софт в нём хуже отсутствия: он объявляет обязательства по чужим
    лицензиям, которых образ уже не несёт.
    """
    notices = (WEBTOP / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    dockerfile = (WEBTOP / "Dockerfile").read_text(encoding="utf-8")

    lowered = notices.lower()
    assert "kasmvnc" not in lowered
    assert "novnc" not in lowered
    # Обещания про сборку веб-клиента: builder-стадии в образе больше нет.
    assert "web client" not in lowered

    # То, что реально ставится, обязано быть объявлено.
    assert "RustDesk" in notices
    assert "Vision" in notices
    assert "Firefox" in notices
    for token in ("RUSTDESK_VERSION", "FIREFOX_VERSION"):
        assert token in dockerfile
