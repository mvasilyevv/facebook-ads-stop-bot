#!/usr/bin/env bash
# Shared durable fence for every browser/Vision stop, restart and task claim.
#
# This file is sourced by the desktop release, healer and runtime wrapper.  A
# caller either owns a renewable lease or adopts an owner supplied by its
# parent through FB_AGENT_BROWSER_MAINTENANCE_OWNER.  PostgreSQL is the source
# of truth; the file lock alone is never considered a task-claim fence.

readonly BROWSER_MAINTENANCE_LEASE_SECONDS=45
readonly BROWSER_MAINTENANCE_RENEW_SECONDS=10
readonly BROWSER_MAINTENANCE_ACQUIRE_WAIT_SECONDS=55
readonly BROWSER_MAINTENANCE_ACQUIRE_POLL_SECONDS=2
readonly BROWSER_MAINTENANCE_QUIESCENCE_WAIT_SECONDS=55
readonly BROWSER_MAINTENANCE_QUIESCENCE_POLL_SECONDS=2

BROWSER_MAINTENANCE_OWNER=""
BROWSER_MAINTENANCE_OWNED=false
BROWSER_MAINTENANCE_HELD=false
BROWSER_MAINTENANCE_RENEW_PID=""
BROWSER_MAINTENANCE_RUNTIME_DIR=""

browser_maintenance_deadline_remaining() {
  local -r deadline="${FB_AGENT_BROWSER_MAINTENANCE_DEADLINE_EPOCH:-}"
  local now=0
  local remaining=0
  [[ -n "$deadline" ]] || {
    printf '2147483647\n'
    return 0
  }
  [[ "$deadline" =~ ^[0-9]+$ ]] || return 1
  now="$(date +%s)"
  remaining=$((deadline - now))
  ((remaining > 1)) || return 1
  # Leave one second for the caller to observe failure and persist its durable
  # rollback outcome before the shared absolute deadline.
  printf '%s\n' "$((remaining - 1))"
}

browser_maintenance_timeout_cap() {
  local -r requested="$1"
  local remaining=""
  remaining="$(browser_maintenance_deadline_remaining)" || return 1
  if ((remaining < requested)); then
    printf '%s\n' "$remaining"
  else
    printf '%s\n' "$requested"
  fi
}

browser_maintenance_postgres_container() {
  local command_timeout=""
  command_timeout="$(browser_maintenance_timeout_cap 10)" || return 1
  timeout --signal=KILL "$command_timeout" docker ps \
    --filter label=com.docker.compose.service=postgres \
    --format '{{.Names}} {{.Label "com.docker.compose.project"}}' \
    | awk '
        $2 == "fb_agent_infra" {count += 1; name = $1}
        END {
          if (count == 1) {
            print name
          } else {
            exit 1
          }
        }
      '
}

browser_maintenance_require_owner() {
  [[ "$BROWSER_MAINTENANCE_OWNER" =~ ^[0-9a-f]{32}$ ]] \
    || {
      printf 'ERROR: browser maintenance owner is invalid\n' >&2
      return 1
    }
}

browser_maintenance_acquire() {
  local container=""
  local acquired_owner=""
  local -i acquire_deadline=0
  local -i attempt_timeout=0
  local -i remaining_seconds=0
  local -i sleep_seconds=0
  local global_remaining=""
  acquire_deadline=$((SECONDS + BROWSER_MAINTENANCE_ACQUIRE_WAIT_SECONDS))
  container="$(browser_maintenance_postgres_container)"
  [[ -n "$container" ]] \
    || {
      printf 'ERROR: production PostgreSQL container is not running\n' >&2
      return 1
    }
  BROWSER_MAINTENANCE_OWNER="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
  browser_maintenance_require_owner || return 1
  while :; do
    remaining_seconds=$((acquire_deadline - SECONDS))
    if ((remaining_seconds <= 0)); then
      printf 'ERROR: durable browser maintenance lease was not acquired within %ss\n' \
        "$BROWSER_MAINTENANCE_ACQUIRE_WAIT_SECONDS" >&2
      return 1
    fi
    attempt_timeout=10
    if ((remaining_seconds < attempt_timeout)); then
      attempt_timeout="$remaining_seconds"
    fi
    global_remaining="$(browser_maintenance_timeout_cap "$attempt_timeout")" \
      || return 1
    attempt_timeout="$global_remaining"
    acquired_owner=""
    # A non-blocking advisory attempt keeps this outer wait strictly bounded.
    # The same generated owner is reused so an ambiguous successful write can
    # be observed safely by the next attempt.
    # shellcheck disable=SC2016 # Positional/env values expand in the container shell.
    if ! acquired_owner="$(timeout --signal=KILL "$attempt_timeout" \
      docker exec "$container" sh -eu -c \
      'exec psql --no-psqlrc --quiet --tuples-only --no-align \
        --set ON_ERROR_STOP=1 --set=owner="$1" --set=lease_seconds="$2" \
        --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' \
      sh "$BROWSER_MAINTENANCE_OWNER" "$BROWSER_MAINTENANCE_LEASE_SECONDS" <<'SQL'
BEGIN;
WITH lock_attempt AS (
  SELECT pg_try_advisory_xact_lock(
    hashtext('fb-agent'),
    hashtext('browser-maintenance')
  ) AS acquired
),
lease_write AS (
  INSERT INTO system_config (key, value, description)
  SELECT
    'browser_maintenance',
    jsonb_build_object(
      'owner', :'owner',
      'expires_at',
      clock_timestamp() + make_interval(secs => :'lease_seconds'::integer)
    ),
    'Blocks new browser-backed task claims during desktop maintenance'
  FROM lock_attempt
  WHERE acquired
  ON CONFLICT (key) DO UPDATE
  SET value = EXCLUDED.value,
      description = EXCLUDED.description,
      updated_at = clock_timestamp()
  WHERE system_config.value->>'owner' = :'owner'
     OR COALESCE(
          (system_config.value->>'expires_at')::timestamptz,
          '-infinity'::timestamptz
        ) <= clock_timestamp()
  RETURNING value->>'owner' AS owner
)
SELECT lease_write.owner
FROM lease_write;
COMMIT;
SQL
    )"; then
      acquired_owner=""
    fi
    acquired_owner="${acquired_owner//$'\n'/}"
    if [[ "$acquired_owner" == "$BROWSER_MAINTENANCE_OWNER" ]]; then
      break
    fi
    remaining_seconds=$((acquire_deadline - SECONDS))
    if ((remaining_seconds <= 0)); then
      printf 'ERROR: durable browser maintenance lease was not acquired within %ss\n' \
        "$BROWSER_MAINTENANCE_ACQUIRE_WAIT_SECONDS" >&2
      return 1
    fi
    sleep_seconds="$BROWSER_MAINTENANCE_ACQUIRE_POLL_SECONDS"
    if ((remaining_seconds < sleep_seconds)); then
      sleep_seconds="$remaining_seconds"
    fi
    global_remaining="$(browser_maintenance_timeout_cap "$sleep_seconds")" \
      || return 1
    sleep_seconds="$global_remaining"
    sleep "$sleep_seconds"
  done
  BROWSER_MAINTENANCE_OWNED=true
  BROWSER_MAINTENANCE_HELD=true
  export FB_AGENT_BROWSER_MAINTENANCE_OWNER="$BROWSER_MAINTENANCE_OWNER"
}

browser_maintenance_adopt() {
  local -r owner="$1"
  BROWSER_MAINTENANCE_OWNER="$owner"
  browser_maintenance_require_owner || return 1
  BROWSER_MAINTENANCE_OWNED=false
  browser_maintenance_assert_held || return 1
  BROWSER_MAINTENANCE_HELD=true
  export FB_AGENT_BROWSER_MAINTENANCE_OWNER="$BROWSER_MAINTENANCE_OWNER"
}

browser_maintenance_renew() {
  local container=""
  local renewed=""
  local command_timeout=""
  browser_maintenance_require_owner || return 1
  container="$(browser_maintenance_postgres_container)"
  [[ -n "$container" ]] || return 1
  # shellcheck disable=SC2016 # Positional/env values expand in the container shell.
  command_timeout="$(browser_maintenance_timeout_cap 20)" || return 1
  renewed="$(timeout --signal=TERM "$command_timeout" \
    docker exec "$container" sh -eu -c \
    'exec psql --no-psqlrc --quiet --tuples-only --no-align \
      --set ON_ERROR_STOP=1 --set=owner="$1" --set=lease_seconds="$2" \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' \
    sh "$BROWSER_MAINTENANCE_OWNER" "$BROWSER_MAINTENANCE_LEASE_SECONDS" <<'SQL'
BEGIN;
SELECT pg_advisory_xact_lock(
  hashtext('fb-agent'),
  hashtext('browser-maintenance')
);
UPDATE system_config
SET value = jsonb_set(
      value,
      '{expires_at}',
      to_jsonb(clock_timestamp() + make_interval(secs => :'lease_seconds'::integer))
    ),
    updated_at = clock_timestamp()
WHERE key = 'browser_maintenance'
  AND value->>'owner' = :'owner'
  AND (value->>'expires_at')::timestamptz > clock_timestamp()
RETURNING value->>'owner';
COMMIT;
SQL
)"
  renewed="${renewed//$'\n'/}"
  [[ "$renewed" == "$BROWSER_MAINTENANCE_OWNER" ]]
}

browser_maintenance_assert_held() {
  local container=""
  local held=""
  local command_timeout=""
  browser_maintenance_require_owner || return 1
  if [[ -n "$BROWSER_MAINTENANCE_RUNTIME_DIR" \
    && -e "$BROWSER_MAINTENANCE_RUNTIME_DIR/renewal-failed" ]]; then
    return 1
  fi
  container="$(browser_maintenance_postgres_container)"
  [[ -n "$container" ]] || return 1
  # shellcheck disable=SC2016 # Positional/env values expand in the container shell.
  command_timeout="$(browser_maintenance_timeout_cap 20)" || return 1
  held="$(timeout --signal=TERM "$command_timeout" \
    docker exec "$container" sh -eu -c \
    'exec psql --no-psqlrc --quiet --tuples-only --no-align \
      --set ON_ERROR_STOP=1 --set=owner="$1" \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' \
    sh "$BROWSER_MAINTENANCE_OWNER" <<'SQL'
SELECT EXISTS (
  SELECT 1
  FROM system_config
  WHERE key = 'browser_maintenance'
    AND value->>'owner' = :'owner'
    AND (value->>'expires_at')::timestamptz > clock_timestamp()
);
SQL
)"
  held="${held//$'\n'/}"
  [[ "$held" == t ]]
}

browser_maintenance_quiescence_state() {
  local container=""
  local state=""
  local command_timeout=""
  container="$(browser_maintenance_postgres_container)"
  [[ -n "$container" ]] || return 1
  # shellcheck disable=SC2016 # POSTGRES_* expands in the container shell.
  command_timeout="$(browser_maintenance_timeout_cap 20)" || return 1
  state="$(timeout --signal=TERM "$command_timeout" \
    docker exec "$container" sh -eu -c \
    'exec psql --no-psqlrc --quiet --tuples-only --no-align \
      --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' <<'SQL'
SELECT
  (
    SELECT count(*)::text
    FROM task_queue
    WHERE lower(status) = 'running'
      AND task_type IN ('meta_api_mutation', 'observer_scan', 'campaign_create')
  ) || ':' ||
  (
    SELECT count(*)::text
    FROM browser_operation_leases
    WHERE lease_expires_at > clock_timestamp()
  );
SQL
)"
  state="${state//$'\n'/}"
  [[ "$state" =~ ^[0-9]+:[0-9]+$ ]] || return 1
  printf '%s\n' "$state"
}

browser_maintenance_assert_quiescent() {
  local state=""
  state="$(browser_maintenance_quiescence_state)" || return 1
  [[ "$state" == "0:0" ]] \
    || {
      printf 'ERROR: desktop maintenance requires zero running browser tasks and zero active browser operation leases (current: %s)\n' \
        "$state" >&2
      return 1
    }
}

browser_maintenance_wait_for_quiescence() {
  local state=""
  local -i deadline=0
  local -i remaining_seconds=0
  local -i sleep_seconds=0
  local global_remaining=""
  deadline=$((SECONDS + BROWSER_MAINTENANCE_QUIESCENCE_WAIT_SECONDS))
  while :; do
    browser_maintenance_assert_held \
      && browser_maintenance_renew \
      && browser_maintenance_assert_held \
      || {
        printf 'ERROR: browser maintenance lease was lost while waiting for quiescence\n' >&2
        return 1
      }
    state="$(browser_maintenance_quiescence_state)" \
      || {
        printf 'ERROR: browser maintenance quiescence could not be read\n' >&2
        return 1
      }
    if [[ "$state" == "0:0" ]]; then
      return 0
    fi
    remaining_seconds=$((deadline - SECONDS))
    if ((remaining_seconds <= 0)); then
      printf 'ERROR: browser work did not drain within %ss (current: %s)\n' \
        "$BROWSER_MAINTENANCE_QUIESCENCE_WAIT_SECONDS" "$state" >&2
      return 1
    fi
    sleep_seconds="$BROWSER_MAINTENANCE_QUIESCENCE_POLL_SECONDS"
    if ((remaining_seconds < sleep_seconds)); then
      sleep_seconds="$remaining_seconds"
    fi
    global_remaining="$(browser_maintenance_timeout_cap "$sleep_seconds")" \
      || return 1
    sleep_seconds="$global_remaining"
    sleep "$sleep_seconds"
  done
}

browser_maintenance_checkpoint() {
  # A nested maintenance helper may outlive the shell that originally acquired
  # the lease (for example while Compose is stopping a slow browser).  Renew
  # from the process that is about to cross the next mutation boundary, then
  # re-check quiescence.  This keeps destructive work fail-closed even if the
  # owner's background renewal process has died.
  browser_maintenance_assert_held \
    && browser_maintenance_renew \
    && browser_maintenance_assert_held \
    && browser_maintenance_assert_quiescent
}

browser_maintenance_start_renewal() {
  local main_pid=""
  [[ "$BROWSER_MAINTENANCE_OWNED" == true ]] || return 0
  [[ -z "$BROWSER_MAINTENANCE_RENEW_PID" ]] || return 0
  install -d -m 0700 /run/fb-agent || return 1
  BROWSER_MAINTENANCE_RUNTIME_DIR="$(
    mktemp -d /run/fb-agent/browser-maintenance.XXXXXXXX
  )" || return 1
  main_pid="$BASHPID"
  (
    while sleep "$BROWSER_MAINTENANCE_RENEW_SECONDS"; do
      # SIGKILL/OOM cannot run the owner's EXIT trap. The renewer is a direct
      # child, so Linux re-parents it when the owner dies. Refuse another renew
      # unless /proc still proves that exact parent relationship; the durable
      # row then expires naturally within one lease TTL instead of being held
      # forever by an orphan.
      observed_parent=""
      if [[ -r "/proc/$BASHPID/status" ]]; then
        while IFS=$'\t' read -r key value; do
          if [[ "$key" == "PPid:" ]]; then
            observed_parent="${value//[[:space:]]/}"
            break
          fi
        done <"/proc/$BASHPID/status"
      fi
      if [[ "$observed_parent" != "$main_pid" ]] \
        || ! kill -0 "$main_pid" >/dev/null 2>&1; then
        exit 0
      fi
      if ! browser_maintenance_renew; then
        : >"$BROWSER_MAINTENANCE_RUNTIME_DIR/renewal-failed"
        kill -TERM "$main_pid" >/dev/null 2>&1 || true
        exit 1
      fi
    done
  ) &
  BROWSER_MAINTENANCE_RENEW_PID=$!
}

browser_maintenance_release() {
  local container=""
  local released=""
  local command_timeout=""
  browser_maintenance_require_owner || return 1
  container="$(browser_maintenance_postgres_container)"
  [[ -n "$container" ]] || return 1
  # shellcheck disable=SC2016 # Positional/env values expand in the container shell.
  command_timeout="$(browser_maintenance_timeout_cap 20)" || return 1
  if ! released="$(timeout --signal=TERM "$command_timeout" \
    docker exec "$container" sh -eu -c \
    'exec psql --no-psqlrc --quiet --tuples-only --no-align \
      --set ON_ERROR_STOP=1 --set=owner="$1" \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' \
    sh "$BROWSER_MAINTENANCE_OWNER" <<'SQL'
BEGIN;
SELECT pg_advisory_xact_lock(
  hashtext('fb-agent'),
  hashtext('browser-maintenance')
);
DELETE FROM system_config
WHERE key = 'browser_maintenance'
  AND value->>'owner' = :'owner'
  AND (value->>'expires_at')::timestamptz > clock_timestamp()
RETURNING key;
COMMIT;
SQL
  )"; then
    return 1
  fi
  released="${released//$'\n'/}"
  [[ "$released" == browser_maintenance ]] || return 1
  BROWSER_MAINTENANCE_HELD=false
}

browser_maintenance_stop_renewal() {
  if [[ -n "$BROWSER_MAINTENANCE_RENEW_PID" ]]; then
    kill "$BROWSER_MAINTENANCE_RENEW_PID" >/dev/null 2>&1 || true
    wait "$BROWSER_MAINTENANCE_RENEW_PID" >/dev/null 2>&1 || true
    BROWSER_MAINTENANCE_RENEW_PID=""
  fi
}

browser_maintenance_leave() {
  local release_failed=false
  browser_maintenance_stop_renewal
  if [[ "$BROWSER_MAINTENANCE_HELD" == true \
    && "$BROWSER_MAINTENANCE_OWNED" == true ]]; then
    browser_maintenance_release || release_failed=true
  fi
  if [[ -n "$BROWSER_MAINTENANCE_RUNTIME_DIR" ]]; then
    rm -rf -- "$BROWSER_MAINTENANCE_RUNTIME_DIR"
    BROWSER_MAINTENANCE_RUNTIME_DIR=""
  fi
  [[ "$release_failed" == false ]]
}

browser_maintenance_enter() {
  local -r inherited_owner="${FB_AGENT_BROWSER_MAINTENANCE_OWNER:-}"
  command -v timeout >/dev/null 2>&1 \
    || {
      printf 'ERROR: timeout is required for the durable browser maintenance lease\n' >&2
      return 1
    }
  if [[ -n "$inherited_owner" ]]; then
    browser_maintenance_adopt "$inherited_owner" || return 1
  else
    browser_maintenance_acquire || return 1
    if ! browser_maintenance_start_renewal; then
      browser_maintenance_leave || true
      return 1
    fi
  fi
  if ! browser_maintenance_wait_for_quiescence; then
    if [[ "$BROWSER_MAINTENANCE_OWNED" == true ]]; then
      browser_maintenance_leave || {
        printf 'ERROR: browser maintenance lease could not be released after drain failure\n' >&2
      }
    fi
    return 1
  fi
  browser_maintenance_assert_held
}
