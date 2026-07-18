#!/usr/bin/env bash
set -Eeuo pipefail

readonly password_file=/run/kasmvnc/.kasmpasswd
readonly kasm_display=:10
readonly source_display=:1

require_env() {
  local name=$1
  if [[ -z "${!name:-}" ]]; then
    echo "Required environment variable is empty: ${name}" >&2
    exit 64
  fi
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ -n "${proxy_pid:-}" ]]; then
    kill -TERM "${proxy_pid}" 2>/dev/null || true
  fi
  if [[ -n "${server_pid:-}" ]]; then
    kill -TERM "${server_pid}" 2>/dev/null || true
  fi
  wait "${proxy_pid:-}" "${server_pid:-}" 2>/dev/null || true
  exit "${status}"
}
trap cleanup EXIT INT TERM

require_env DESKTOP_KASM_SERVICE_USER
require_env DESKTOP_KASM_SERVICE_PASSWORD
if ((${#DESKTOP_KASM_SERVICE_PASSWORD} < 16)); then
  echo "DESKTOP_KASM_SERVICE_PASSWORD must contain at least 16 characters" >&2
  exit 64
fi

install -d -m 0700 /run/kasmvnc
umask 077
printf '%s\n%s\n' \
  "${DESKTOP_KASM_SERVICE_PASSWORD}" \
  "${DESKTOP_KASM_SERVICE_PASSWORD}" \
  | kasmvncpasswd -u "${DESKTOP_KASM_SERVICE_USER}" -w "${password_file}"
# kasmvncserver performs its preflight against the conventional user path
# before translating the system YAML, so point that path at the tmpfs secret.
ln -sfn "${password_file}" /root/.kasmpasswd

# The X11 socket lives in a named volume shared with webtop. Docker can stop a
# previous sidecar before Xvnc removes X10, so force-recreate must distinguish a
# live display from an orphaned socket instead of entering a restart loop.
if DISPLAY="${kasm_display}" xdpyinfo >/dev/null 2>&1; then
  printf 'Display %s is already served by another process\n' "${kasm_display}" >&2
  exit 1
fi
rm -f -- /tmp/.X11-unix/X10 /tmp/.X10-lock

# The official wrapper converts the locked YAML policy to Xvnc arguments.
# `-noxstartup` keeps this display transport-only; Vision remains on :1.
kasmvncserver "${kasm_display}" -noxstartup
readonly server_pid_file="/root/.vnc/$(hostname):10.pid"
if [[ ! -s "${server_pid_file}" ]]; then
  echo "KasmVNC did not create ${server_pid_file}" >&2
  exit 1
fi
server_pid="$(<"${server_pid_file}")"

for _ in {1..100}; do
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    echo "KasmVNC exited before display ${kasm_display} became ready" >&2
    exit 1
  fi
  if DISPLAY="${kasm_display}" xdpyinfo >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
if ! DISPLAY="${kasm_display}" xdpyinfo >/dev/null 2>&1; then
  echo "Display ${kasm_display} did not become ready" >&2
  exit 1
fi

# Resize is deliberately disabled: do not add kasmxproxy's -r flag.
kasmxproxy -a "${source_display}" -v "${kasm_display}" -f 30 &
proxy_pid=$!

while kill -0 "${server_pid}" 2>/dev/null && kill -0 "${proxy_pid}" 2>/dev/null; do
  sleep 1
done
echo "KasmVNC or kasmxproxy exited unexpectedly" >&2
exit 1
