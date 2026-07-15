#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly VISION_CONTAINER="${VISION_CONTAINER:-vision-webtop}"
readonly TIMEOUT_SECONDS="${VISION_WAIT_TIMEOUT_SECONDS:-180}"
readonly POLL_SECONDS="${VISION_WAIT_INTERVAL_SECONDS:-2}"

[[ "$TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || {
  printf 'ERROR: VISION_WAIT_TIMEOUT_SECONDS must be a non-negative integer\n' >&2
  exit 2
}
[[ "$POLL_SECONDS" =~ ^[0-9]+$ ]] || {
  printf 'ERROR: VISION_WAIT_INTERVAL_SECONDS must be a non-negative integer\n' >&2
  exit 2
}
command -v docker >/dev/null 2>&1 || {
  printf 'ERROR: docker is not installed\n' >&2
  exit 1
}

readonly DEADLINE=$((SECONDS + TIMEOUT_SECONDS))
while :; do
  running="$(
    docker inspect --format '{{.State.Running}}' "$VISION_CONTAINER" 2>/dev/null
  )" || running=""
  if [[ "$running" == "true" ]]; then
    printf 'Vision namespace container is running: %s\n' "$VISION_CONTAINER"
    exit 0
  fi
  if ((SECONDS >= DEADLINE)); then
    break
  fi
  sleep "$POLL_SECONDS"
done

printf 'ERROR: Vision namespace container did not become running within %ss: %s\n' \
  "$TIMEOUT_SECONDS" "$VISION_CONTAINER" >&2
exit 1
