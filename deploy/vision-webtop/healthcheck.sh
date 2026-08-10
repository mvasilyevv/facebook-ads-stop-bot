#!/usr/bin/env bash
set -Eeuo pipefail

readonly display=:1

DISPLAY="${display}" xdpyinfo 2>/dev/null \
  | grep -Eq 'dimensions:[[:space:]]+1366x768'
pgrep -f 'X(kasmvnc|vnc).*:1' >/dev/null
pgrep -x Vision >/dev/null
curl --fail --silent --show-error \
  --user "${DESKTOP_KASM_SERVICE_USER}:${DESKTOP_KASM_SERVICE_PASSWORD}" \
  --output /dev/null \
  http://127.0.0.1:8444/
