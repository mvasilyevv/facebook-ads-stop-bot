#!/usr/bin/env bash
set -Eeuo pipefail

pgrep -f 'X(kasmvnc|vnc).*:10' >/dev/null
pgrep -x kasmxproxy >/dev/null
curl --fail --silent --show-error \
  --user "${DESKTOP_KASM_SERVICE_USER}:${DESKTOP_KASM_SERVICE_PASSWORD}" \
  --output /dev/null \
  http://127.0.0.1:8444/
