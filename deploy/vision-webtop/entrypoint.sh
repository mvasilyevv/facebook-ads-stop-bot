#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly display=:1
readonly runtime_user=vision
readonly config_home=/config

require_env() {
  local name=$1
  if [[ -z "${!name:-}" ]]; then
    printf 'Required environment variable is empty: %s\n' "${name}" >&2
    exit 64
  fi
}

require_numeric_id() {
  local name=$1
  local value=$2
  if [[ ! "${value}" =~ ^[1-9][0-9]{0,8}$ ]]; then
    printf '%s must be a positive numeric id\n' "${name}" >&2
    exit 64
  fi
}

cleanup() {
  local status=$?
  local child=""
  trap - EXIT INT TERM
  for child in "${rustdesk_pid:-}" "${fit_pid:-}" "${vision_pid:-}" "${desktop_pid:-}" "${server_pid:-}"; do
    if [[ -n "${child}" ]]; then
      kill -TERM "${child}" 2>/dev/null || true
    fi
  done
  for child in "${rustdesk_pid:-}" "${fit_pid:-}" "${vision_pid:-}" "${desktop_pid:-}" "${server_pid:-}"; do
    if [[ -n "${child}" ]]; then
      wait "${child}" 2>/dev/null || true
    fi
  done
  exit "${status}"
}
trap cleanup EXIT INT TERM

# Веб-канала больше нет, поэтому нативный обязателен: стол без единого пути
# доступа — недостижимая машина. Отказ на старте не даёт ей стать такой молча.
require_env DESKTOP_RUSTDESK_PASSWORD
require_env DESKTOP_RUSTDESK_SERVER
require_env DESKTOP_RUSTDESK_KEY
if ((${#DESKTOP_RUSTDESK_PASSWORD} < 16)); then
  printf 'DESKTOP_RUSTDESK_PASSWORD must contain at least 16 characters\n' >&2
  exit 64
fi

readonly requested_uid=${PUID:-1000}
readonly requested_gid=${PGID:-1000}
require_numeric_id PUID "${requested_uid}"
require_numeric_id PGID "${requested_gid}"

if [[ "$(id -g "${runtime_user}")" != "${requested_gid}" ]]; then
  groupmod --gid "${requested_gid}" "${runtime_user}"
fi
if [[ "$(id -u "${runtime_user}")" != "${requested_uid}" ]]; then
  usermod --uid "${requested_uid}" "${runtime_user}"
fi

install -d -o "${requested_uid}" -g "${requested_gid}" -m 0700 \
  "${config_home}" "${config_home}/.cache" "${config_home}/.config" \
  "${config_home}/.local" "${config_home}/Desktop"

# Ярлык браузера прямо на столе: меню XFCE его тоже показывает, но искать там
# оператору незачем. Раскладываем на каждом старте — ярлык наш, а не
# пользовательский, и должен переживать смену версии.
install -o "${requested_uid}" -g "${requested_gid}" -m 0700 \
  /usr/share/applications/firefox.desktop "${config_home}/Desktop/firefox.desktop"

umask 077

if DISPLAY="${display}" xdpyinfo >/dev/null 2>&1; then
  printf 'Display %s is already served by another process\n' "${display}" >&2
  exit 1
fi
rm -f -- /tmp/.X11-unix/X1 /tmp/.X1-lock

# Размер стола фиксирован: подстраивать его больше не под что — оператор
# видит стол через нативный клиент, который масштабирует картинку сам.
gosu "${runtime_user}" env HOME="${config_home}" \
  Xvfb "${display}" -screen 0 1920x1080x24 -nolisten tcp &
server_pid=$!

for _ in {1..150}; do
  if DISPLAY="${display}" xdpyinfo >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
if ! kill -0 "${server_pid}" 2>/dev/null || ! DISPLAY="${display}" xdpyinfo >/dev/null 2>&1; then
  printf 'X display %s did not become ready\n' "${display}" >&2
  exit 1
fi

gosu "${runtime_user}" env \
  DISPLAY="${display}" HOME="${config_home}" XDG_CONFIG_HOME="${config_home}/.config" \
  XDG_CACHE_HOME="${config_home}/.cache" \
  dbus-run-session -- xfce4-session &
desktop_pid=$!

for _ in {1..100}; do
  if DISPLAY="${display}" wmctrl -m >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
if ! kill -0 "${desktop_pid}" 2>/dev/null || ! DISPLAY="${display}" wmctrl -m >/dev/null 2>&1; then
  printf 'XFCE session did not become ready\n' >&2
  exit 1
fi

# Caps Lock remains a host input-source switch and must not toggle remote case.
gosu "${runtime_user}" env DISPLAY="${display}" xmodmap -e 'clear Lock'
gosu "${runtime_user}" env DISPLAY="${display}" xmodmap -e 'keycode 66 = NoSymbol'

gosu "${runtime_user}" env \
  DISPLAY="${display}" HOME="${config_home}" XDG_CONFIG_HOME="${config_home}/.config" \
  XDG_CACHE_HOME="${config_home}/.cache" \
  /usr/bin/Vision &
vision_pid=$!

gosu "${runtime_user}" env DISPLAY="${display}" /usr/local/bin/vision-window-fit.sh &
fit_pid=$!

# Нативный канал к тому же столу. Нужен ради буфера обмена: в браузере на
# iPhone он не работает и не может — WebKit требует свежего жеста, а жест
# протухает раньше, чем VNC-клиент сходит на сервер и вернётся.
#
# Пустой пароль означает «канал не нужен». Открытый наружу порт не должен
# появляться сам по себе, поэтому включает его владелец, задав пароль.
rustdesk_password=${DESKTOP_RUSTDESK_PASSWORD}
{
  # Стол у нас настоящий, с framebuffer, поэтому headless-режим RustDesk с его
  # известными болячками не включаем: захватывать есть что.
  rustdesk_env=(
    DISPLAY="${display}" HOME="${config_home}"
    XDG_CONFIG_HOME="${config_home}/.config" XDG_CACHE_HOME="${config_home}/.cache"
  )

  # Конфиг раскладываем сами, а не через `rustdesk --option`: на живом
  # контейнере опция до файла не доехала, а запущенный клиент перезаписал файл
  # своим состоянием. Файл под нашим контролем — состояние предсказуемо.
  readonly rustdesk_config_dir="${config_home}/.config/rustdesk"
  install -d -o "${requested_uid}" -g "${requested_gid}" -m 0700 "${rustdesk_config_dir}"
  install -o "${requested_uid}" -g "${requested_gid}" -m 0600 /dev/null \
    "${rustdesk_config_dir}/RustDesk2.toml"
  cat >"${rustdesk_config_dir}/RustDesk2.toml" <<RUSTDESK_CONFIG
rendezvous_server = '${DESKTOP_RUSTDESK_SERVER}'
nat_type = 0
serial = 0

[options]
custom-rendezvous-server = '${DESKTOP_RUSTDESK_SERVER}'
relay-server = '${DESKTOP_RUSTDESK_SERVER}'
key = '${DESKTOP_RUSTDESK_KEY}'
verification-method = 'use-permanent-password'
RUSTDESK_CONFIG
  chown "${requested_uid}:${requested_gid}" "${rustdesk_config_dir}/RustDesk2.toml"

  gosu "${runtime_user}" env "${rustdesk_env[@]}" rustdesk --password "${rustdesk_password}" \
    >/dev/null 2>&1 || true

  rustdesk_supervisor() {
    # Единственный канал к столу. Если он упадёт, оператор не должен потерять
    # доступ вместе с открытым кабинетом: поднимаем на месте, не роняя стол.
    #
    # Нужны оба процесса: сервис принимает подключения, а основной клиент
    # держит сессию и показывает оператору ID прямо на столе.
    local service_pid=""
    local client_pid=""
    while true; do
      gosu "${runtime_user}" env "${rustdesk_env[@]}" rustdesk --service &
      service_pid=$!
      sleep 5
      gosu "${runtime_user}" env "${rustdesk_env[@]}" rustdesk &
      client_pid=$!
      wait -n "${service_pid}" "${client_pid}" 2>/dev/null || true
      kill -TERM "${service_pid}" "${client_pid}" 2>/dev/null || true
      wait "${service_pid}" "${client_pid}" 2>/dev/null || true
      sleep 5
    done
  }
  rustdesk_supervisor &
  rustdesk_pid=$!
}

while true; do
  if ! kill -0 "${desktop_pid}" 2>/dev/null; then
    printf 'XFCE session exited unexpectedly\n' >&2
    exit 1
  fi
  if ! kill -0 "${vision_pid}" 2>/dev/null; then
    printf 'Vision exited unexpectedly\n' >&2
    exit 1
  fi
  if ! kill -0 "${fit_pid}" 2>/dev/null; then
    printf 'Vision window supervisor exited unexpectedly\n' >&2
    exit 1
  fi
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    printf 'X display exited unexpectedly\n' >&2
    exit 1
  fi
  sleep 1
done
