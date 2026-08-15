#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly display=:1
readonly password_file=/config/.kasmpasswd
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
  for child in "${fit_pid:-}" "${vision_pid:-}" "${desktop_pid:-}"; do
    if [[ -n "${child}" ]]; then
      kill -TERM "${child}" 2>/dev/null || true
    fi
  done
  gosu "${runtime_user}" env HOME="${config_home}" kasmvncserver "${display}" -kill \
    >/dev/null 2>&1 || true
  for child in "${fit_pid:-}" "${vision_pid:-}" "${desktop_pid:-}"; do
    if [[ -n "${child}" ]]; then
      wait "${child}" 2>/dev/null || true
    fi
  done
  exit "${status}"
}
trap cleanup EXIT INT TERM

require_env DESKTOP_KASM_SERVICE_USER
require_env DESKTOP_KASM_SERVICE_PASSWORD
if ((${#DESKTOP_KASM_SERVICE_PASSWORD} < 16)); then
  printf 'DESKTOP_KASM_SERVICE_PASSWORD must contain at least 16 characters\n' >&2
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
  "${config_home}/.local" "${config_home}/.vnc"
install -d -o "${requested_uid}" -g "${requested_gid}" -m 0700 /run/kasmvnc

# Конфиг из образа лежит в /etc/kasmvnc, но профиль монтируется поверх HOME, и
# на пустом профиле сервер создаёт там свой дефолт — он перекрывает системный,
# теряет путь к файлу паролей и падает с «No users configured». Раскладываем
# управляемый конфиг в профиль на каждом старте: он наш, а не пользовательский.
install -o "${requested_uid}" -g "${requested_gid}" -m 0600 \
  /etc/kasmvnc/kasmvnc.yaml "${config_home}/.vnc/kasmvnc.yaml"

umask 077
printf '%s\n%s\n' \
  "${DESKTOP_KASM_SERVICE_PASSWORD}" \
  "${DESKTOP_KASM_SERVICE_PASSWORD}" \
  | gosu "${runtime_user}" kasmvncpasswd \
      -u "${DESKTOP_KASM_SERVICE_USER}" -w "${password_file}"

if DISPLAY="${display}" xdpyinfo >/dev/null 2>&1; then
  printf 'Display %s is already served by another process\n' "${display}" >&2
  exit 1
fi
rm -f -- /tmp/.X11-unix/X1 /tmp/.X1-lock

gosu "${runtime_user}" env \
  DISPLAY="${display}" HOME="${config_home}" XDG_CONFIG_HOME="${config_home}/.config" \
  XDG_CACHE_HOME="${config_home}/.cache" \
  kasmvncserver "${display}" -noxstartup -geometry 1366x768 -depth 24

readonly server_pid_file="${config_home}/.vnc/$(hostname):1.pid"
for _ in {1..150}; do
  if [[ -s "${server_pid_file}" ]] && DISPLAY="${display}" xdpyinfo >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
if [[ ! -s "${server_pid_file}" ]] || ! DISPLAY="${display}" xdpyinfo >/dev/null 2>&1; then
  printf 'KasmVNC display %s did not become ready\n' "${display}" >&2
  exit 1
fi
readonly server_pid="$(<"${server_pid_file}")"
if [[ ! "${server_pid}" =~ ^[1-9][0-9]*$ ]] || ! kill -0 "${server_pid}" 2>/dev/null; then
  printf 'KasmVNC did not publish a live numeric pid\n' >&2
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
    printf 'KasmVNC exited unexpectedly\n' >&2
    exit 1
  fi
  sleep 1
done
