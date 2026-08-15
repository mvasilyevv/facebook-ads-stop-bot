#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly display=${DISPLAY:-:1}
# Окно браузера Vision держим фиксированным, хотя стол теперь тянется за окном
# оператора. Размер видимой области — часть отпечатка кабинета: если он скачет
# от сессии к сессии вслед за окном браузера оператора, профиль выглядит
# страннее обычного пользователя. 1366x768 — распространённое разрешение
# ноутбука, оно и было зашито раньше на весь стол.
readonly vision_width=1366
readonly vision_height=768
last_geometry=""

list_vision_windows() {
  local window_id=""
  local wm_class=""
  while IFS= read -r window_id; do
    [[ -n "${window_id}" ]] || continue
    wm_class="$(xprop -id "${window_id}" WM_CLASS 2>/dev/null || true)"
    [[ "${wm_class}" == *'/config/.local/share/Vision/profiles/'* ]] || continue
    [[ "${wm_class}" == *'"Chromium-browser"'* ]] || continue
    printf '%s\n' "${window_id}"
  done < <(
    xprop -root _NET_CLIENT_LIST 2>/dev/null \
      | sed -n 's/.*# //p' \
      | tr ',' '\n' \
      | sed 's/^[[:space:]]*//; /^[[:space:]]*$/d'
  )
}

while true; do
  geometry="$(xdpyinfo -display "${display}" 2>/dev/null | awk '/dimensions:/ {print $2; exit}')"
  screen_width="${geometry%%x*}"
  screen_height="${geometry##*x}"
  geometry_changed=false
  if [[ -n "${geometry}" && "${geometry}" != "${last_geometry}" ]]; then
    geometry_changed=true
    last_geometry="${geometry}"
  fi

  # Стол меньше окна Vision бывает на телефоне: там разворачиваем во весь стол,
  # потому что иначе часть окна просто недостижима. Работать с кабинетом с
  # телефона всё равно никто не будет, а посмотреть — нужно.
  fits=false
  if [[ "${screen_width}" =~ ^[0-9]+$ && "${screen_height}" =~ ^[0-9]+$ ]] \
    && ((screen_width >= vision_width)) && ((screen_height >= vision_height)); then
    fits=true
  fi

  while IFS= read -r window_id; do
    state="$(xprop -id "${window_id}" _NET_WM_STATE 2>/dev/null || true)"
    if [[ "${fits}" == true ]]; then
      # Точный размер задаётся только развёрнутому окну: снимаем максимизацию,
      # иначе менеджер окон вернёт его на весь экран.
      if [[ "${geometry_changed}" == true \
        || "${state}" == *'_NET_WM_STATE_MAXIMIZED_VERT'* \
        || "${state}" == *'_NET_WM_STATE_MAXIMIZED_HORZ'* ]]; then
        wmctrl -i -r "${window_id}" -b remove,maximized_vert,maximized_horz \
          >/dev/null 2>&1 || true
        offset_x=$(((screen_width - vision_width) / 2))
        offset_y=$(((screen_height - vision_height) / 2))
        wmctrl -i -r "${window_id}" -e "0,${offset_x},${offset_y},${vision_width},${vision_height}" \
          >/dev/null 2>&1 || true
      fi
    else
      if [[ "${geometry_changed}" == true ]]; then
        wmctrl -i -r "${window_id}" -b remove,maximized_vert,maximized_horz \
          >/dev/null 2>&1 || true
      fi
      if [[ "${geometry_changed}" == true \
        || "${state}" != *'_NET_WM_STATE_MAXIMIZED_VERT'* \
        || "${state}" != *'_NET_WM_STATE_MAXIMIZED_HORZ'* ]]; then
        wmctrl -i -r "${window_id}" -b add,maximized_vert,maximized_horz \
          >/dev/null 2>&1 || true
      fi
    fi
  done < <(list_vision_windows)

  sleep 2
done
