#!/usr/bin/env bash
set -Eeuo pipefail

readonly display=:1

# Вывод xdpyinfo читаем целиком и только потом сверяем. В конвейере
# `xdpyinfo | grep -q` grep выходит на первом совпадении, xdpyinfo получает
# SIGPIPE, и при pipefail весь конвейер возвращает 141 — здоровый рабочий стол
# помечался unhealthy, хотя каждая проверка по отдельности проходила.
dpyinfo="$(DISPLAY="${display}" xdpyinfo 2>/dev/null)"
readonly dpyinfo
grep -Eq 'dimensions:[[:space:]]+[1-9][0-9]{2,4}x[1-9][0-9]{2,4}' <<<"${dpyinfo}"
pgrep -x Xvfb >/dev/null
pgrep -x Vision >/dev/null
# Канал к столу единственный: без него машина недостижима, и это не «деградация»,
# а неработоспособность — контейнер обязан сообщить о ней как о болезни.
pgrep -x rustdesk >/dev/null
