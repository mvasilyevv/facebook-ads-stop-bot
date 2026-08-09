#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly display=${DISPLAY:-:1}
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
  geometry_changed=false
  if [[ -n "${geometry}" && "${geometry}" != "${last_geometry}" ]]; then
    geometry_changed=true
    last_geometry="${geometry}"
  fi

  while IFS= read -r window_id; do
    state="$(xprop -id "${window_id}" _NET_WM_STATE 2>/dev/null || true)"
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
  done < <(list_vision_windows)

  sleep 2
done
