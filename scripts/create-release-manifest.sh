#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

IMAGE_BASE=""
IMAGE_TAG=""
OUTPUT=""
REDIS_SOURCE="${REDIS_SOURCE_IMAGE:-redis:7-alpine}"
DESKTOP_WEBTOP_IMAGE=""
DESKTOP_KASMVNC_IMAGE=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() {
  if [[ -n "${TEMP_FILE:-}" ]]; then
    rm -f -- "$TEMP_FILE"
  fi
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --image-base) IMAGE_BASE="${2:?missing value}"; shift 2 ;;
    --tag) IMAGE_TAG="${2:?missing value}"; shift 2 ;;
    --output) OUTPUT="${2:?missing value}"; shift 2 ;;
    --redis-image) REDIS_SOURCE="${2:?missing value}"; shift 2 ;;
    --desktop-webtop-image) DESKTOP_WEBTOP_IMAGE="${2:?missing value}"; shift 2 ;;
    --desktop-kasmvnc-image) DESKTOP_KASMVNC_IMAGE="${2:?missing value}"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$IMAGE_BASE" ]] || die "--image-base is required"
[[ "$IMAGE_TAG" =~ ^[A-Za-z0-9._-]+$ ]] || die "--tag is invalid"
[[ -n "$OUTPUT" ]] || die "--output is required"
[[ "$DESKTOP_WEBTOP_IMAGE" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
  || die "--desktop-webtop-image must be image@sha256"
[[ "$DESKTOP_KASMVNC_IMAGE" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
  || die "--desktop-kasmvnc-image must be image@sha256"
command -v docker >/dev/null 2>&1 || die "docker is not installed"
docker buildx version >/dev/null 2>&1 || die "docker buildx is unavailable"

resolve_image() {
  local -r image="$1"
  local digest=""
  if [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]]; then
    printf '%s\n' "$image"
    return
  fi
  digest="$(docker buildx imagetools inspect "$image" \
    | awk '$1 == "Digest:" {print $2; exit}')"
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || die "cannot resolve digest for $image"
  # Strip only the final tag separator; registry hosts may include a port.
  printf '%s@%s\n' "${image%:*}" "$digest"
}

TEMP_FILE="$(mktemp "${OUTPUT}.XXXXXX")"
{
  printf 'RELEASE_ID=%s\n' "$IMAGE_TAG"
  printf 'API_IMAGE=%s\n' "$(resolve_image "${IMAGE_BASE}-api:${IMAGE_TAG}")"
  printf 'WORKERS_IMAGE=%s\n' "$(resolve_image "${IMAGE_BASE}-workers:${IMAGE_TAG}")"
  printf 'FRONTEND_IMAGE=%s\n' "$(resolve_image "${IMAGE_BASE}-frontend:${IMAGE_TAG}")"
  printf 'MINI_APP_IMAGE=%s\n' "$(resolve_image "${IMAGE_BASE}-mini-app:${IMAGE_TAG}")"
  printf 'BROWSER_AGENT_IMAGE=%s\n' "$(resolve_image "${IMAGE_BASE}-browser-agent:${IMAGE_TAG}")"
  printf 'DESKTOP_WEBTOP_IMAGE=%s\n' "$DESKTOP_WEBTOP_IMAGE"
  printf 'DESKTOP_KASMVNC_IMAGE=%s\n' "$DESKTOP_KASMVNC_IMAGE"
  printf 'POSTGRES_IMAGE=%s\n' "$(resolve_image "${IMAGE_BASE}-postgres:${IMAGE_TAG}")"
  printf 'REDIS_IMAGE=%s\n' "$(resolve_image "$REDIS_SOURCE")"
} >"$TEMP_FILE"
chmod 0600 "$TEMP_FILE"
mv -- "$TEMP_FILE" "$OUTPUT"
TEMP_FILE=""
printf 'Immutable release manifest created: %s\n' "$OUTPUT"
