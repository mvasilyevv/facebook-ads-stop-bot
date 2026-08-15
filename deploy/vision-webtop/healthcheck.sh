#!/usr/bin/env bash
set -Eeuo pipefail

readonly display=:1

# Вывод xdpyinfo читаем целиком и только потом сверяем. В конвейере
# `xdpyinfo | grep -q` grep выходит на первом совпадении, xdpyinfo получает
# SIGPIPE, и при pipefail весь конвейер возвращает 141 — здоровый рабочий стол
# помечался unhealthy, хотя каждая проверка по отдельности проходила.
dpyinfo="$(DISPLAY="${display}" xdpyinfo 2>/dev/null)"
readonly dpyinfo
# Конкретный размер не проверяем: стол подстраивается под окно оператора, и
# требование ровно 1366x768 объявило бы десктоп больным сразу после первого
# изменения размера. Проверяем то, что действительно нужно: сервер отвечает и
# у экрана осмысленные размеры.
grep -Eq 'dimensions:[[:space:]]+[1-9][0-9]{2,4}x[1-9][0-9]{2,4}' <<<"${dpyinfo}"
pgrep -f 'X(kasmvnc|vnc).*:1' >/dev/null
pgrep -x Vision >/dev/null
curl --fail --silent --show-error \
  --user "${DESKTOP_KASM_SERVICE_USER}:${DESKTOP_KASM_SERVICE_PASSWORD}" \
  --output /dev/null \
  http://127.0.0.1:8444/
