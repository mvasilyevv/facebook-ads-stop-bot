#!/usr/bin/env bash

# Sourceable fail-closed validator for the durable API/browser-agent capability
# file. It never prints or exports the secret value.
browser_control_env_error() {
  printf 'ERROR: browser control environment is unsafe: %s\n' "$1" >&2
}

browser_control_env_require() (
  # Keep callers' tracing state intact while preventing the capability from
  # appearing in xtrace output during validation.
  set +x
  local -r path="${1:-}"
  local -r expected_uid="${2:-${EUID}}"
  local metadata=""
  local line=""
  local maintenance_secret=""
  local autopause_secret=""
  local meta_api_secret=""
  local campaign_creator_secret=""
  local authority_token=""
  local maintenance_count=0
  local autopause_count=0
  local meta_api_count=0
  local campaign_creator_count=0
  local authority_count=0

  [[ -n "$path" ]] \
    || { browser_control_env_error "path is empty"; return 1; }
  [[ "$expected_uid" =~ ^[0-9]+$ ]] \
    || { browser_control_env_error "expected owner is invalid"; return 1; }
  command -v stat >/dev/null 2>&1 \
    || { browser_control_env_error "stat is unavailable"; return 1; }
  [[ -f "$path" && ! -L "$path" ]] \
    || { browser_control_env_error "not a regular non-symlink file"; return 1; }
  metadata="$(
    stat -c '%a:%u' -- "$path" 2>/dev/null \
      || stat -f '%Lp:%u' -- "$path"
  )" \
    || { browser_control_env_error "metadata cannot be read"; return 1; }
  [[ "$metadata" == "600:$expected_uid" ]] \
    || { browser_control_env_error "owner or mode differs from ${expected_uid}:600"; return 1; }

  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ""|\#*) ;;
      BROWSER_MAINTENANCE_CAPABILITY_SECRET=*)
        maintenance_count=$((maintenance_count + 1))
        ((maintenance_count == 1)) \
          || { browser_control_env_error "duplicate capability assignment"; return 1; }
        maintenance_secret="${line#BROWSER_MAINTENANCE_CAPABILITY_SECRET=}"
        [[ "$maintenance_secret" =~ ^[A-Za-z0-9_-]{48,}$ ]] \
          || { browser_control_env_error "capability value is invalid"; return 1; }
        ;;
      BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE=*)
        autopause_count=$((autopause_count + 1))
        ((autopause_count == 1)) \
          || { browser_control_env_error "duplicate autopause capability"; return 1; }
        autopause_secret="${line#*=}"
        [[ "$autopause_secret" =~ ^[A-Za-z0-9_-]{48,}$ ]] \
          || { browser_control_env_error "autopause capability is invalid"; return 1; }
        ;;
      BROWSER_OPERATION_CAPABILITY_SECRET_META_API=*)
        meta_api_count=$((meta_api_count + 1))
        ((meta_api_count == 1)) \
          || { browser_control_env_error "duplicate meta-api capability"; return 1; }
        meta_api_secret="${line#*=}"
        [[ "$meta_api_secret" =~ ^[A-Za-z0-9_-]{48,}$ ]] \
          || { browser_control_env_error "meta-api capability is invalid"; return 1; }
        ;;
      BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR=*)
        campaign_creator_count=$((campaign_creator_count + 1))
        ((campaign_creator_count == 1)) \
          || { browser_control_env_error "duplicate campaign capability"; return 1; }
        campaign_creator_secret="${line#*=}"
        [[ "$campaign_creator_secret" =~ ^[A-Za-z0-9_-]{48,}$ ]] \
          || { browser_control_env_error "campaign capability is invalid"; return 1; }
        ;;
      BROWSER_AUTHORITY_CONSUMER_TOKEN=*)
        authority_count=$((authority_count + 1))
        ((authority_count == 1)) \
          || { browser_control_env_error "duplicate authority credential"; return 1; }
        authority_token="${line#*=}"
        [[ "$authority_token" =~ ^[A-Za-z0-9_-]{48,}$ ]] \
          || { browser_control_env_error "authority credential is invalid"; return 1; }
        ;;
      *)
        browser_control_env_error "unexpected content"
        return 1
        ;;
    esac
  done <"$path"
  ((maintenance_count == 1)) \
    || { browser_control_env_error "maintenance capability assignment is missing"; return 1; }
  ((autopause_count == 1 && meta_api_count == 1 && campaign_creator_count == 1)) \
    || { browser_control_env_error "operation capability keyring is incomplete"; return 1; }
  ((authority_count == 1)) \
    || { browser_control_env_error "authority credential is missing"; return 1; }
  [[ "$maintenance_secret" != "$autopause_secret"
      && "$maintenance_secret" != "$meta_api_secret"
      && "$maintenance_secret" != "$campaign_creator_secret"
      && "$maintenance_secret" != "$authority_token"
      && "$autopause_secret" != "$meta_api_secret"
      && "$autopause_secret" != "$campaign_creator_secret"
      && "$autopause_secret" != "$authority_token"
      && "$meta_api_secret" != "$campaign_creator_secret"
      && "$meta_api_secret" != "$authority_token"
      && "$campaign_creator_secret" != "$authority_token" ]] \
    || { browser_control_env_error "capability values must be independently scoped"; return 1; }
)

browser_scoped_env_require() (
  set +x
  local -r path="${1:-}"
  local -r expected_key="${2:-}"
  local -r expected_uid="${3:-${EUID}}"
  local metadata=""
  local line=""
  local value=""
  local count=0

  case "$expected_key" in
    BROWSER_MAINTENANCE_CAPABILITY_SECRET|BROWSER_OPERATION_CAPABILITY_SECRET|BROWSER_AUTHORITY_CONSUMER_TOKEN) ;;
    *) browser_control_env_error "scoped capability key is invalid"; return 1 ;;
  esac
  [[ -n "$path" ]] \
    || { browser_control_env_error "scoped path is empty"; return 1; }
  [[ "$expected_uid" =~ ^[0-9]+$ ]] \
    || { browser_control_env_error "expected owner is invalid"; return 1; }
  [[ -f "$path" && ! -L "$path" ]] \
    || { browser_control_env_error "scoped file is not regular"; return 1; }
  metadata="$(
    stat -c '%a:%u' -- "$path" 2>/dev/null \
      || stat -f '%Lp:%u' -- "$path"
  )" \
    || { browser_control_env_error "scoped metadata cannot be read"; return 1; }
  [[ "$metadata" == "600:$expected_uid" ]] \
    || { browser_control_env_error "scoped owner or mode differs from ${expected_uid}:600"; return 1; }

  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ""|\#*) ;;
      "$expected_key="*)
        count=$((count + 1))
        ((count == 1)) \
          || { browser_control_env_error "duplicate scoped capability assignment"; return 1; }
        value="${line#*=}"
        [[ "$value" =~ ^[A-Za-z0-9_-]{48,}$ ]] \
          || { browser_control_env_error "scoped capability value is invalid"; return 1; }
        ;;
      *)
        browser_control_env_error "unexpected scoped content"
        return 1
        ;;
    esac
  done <"$path"
  ((count == 1)) \
    || { browser_control_env_error "scoped capability assignment is missing"; return 1; }
)

browser_maintenance_env_require() {
  browser_scoped_env_require \
    "${1:-}" BROWSER_MAINTENANCE_CAPABILITY_SECRET "${2:-${EUID}}"
}

browser_operation_env_require() {
  browser_scoped_env_require \
    "${1:-}" BROWSER_OPERATION_CAPABILITY_SECRET "${2:-${EUID}}"
}

browser_authority_env_require() {
  browser_scoped_env_require \
    "${1:-}" BROWSER_AUTHORITY_CONSUMER_TOKEN "${2:-${EUID}}"
}
