#!/bin/sh
set -eu

readonly_schema_tables="
guacamole_connection_group
guacamole_connection
guacamole_entity
guacamole_user
guacamole_user_group
guacamole_user_group_member
guacamole_sharing_profile
guacamole_connection_parameter
guacamole_sharing_profile_parameter
guacamole_user_attribute
guacamole_user_group_attribute
guacamole_connection_attribute
guacamole_connection_group_attribute
guacamole_sharing_profile_attribute
guacamole_connection_permission
guacamole_connection_group_permission
guacamole_sharing_profile_permission
guacamole_system_permission
guacamole_user_permission
guacamole_user_group_permission
guacamole_connection_history
guacamole_user_history
guacamole_user_password_history
"

: "${PGHOST:?PGHOST is required}"
: "${PGDATABASE:?PGDATABASE is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${DESKTOP_VNC_PASSWORD:?DESKTOP_VNC_PASSWORD is required}"

existing_tables="$(psql --no-psqlrc --tuples-only --no-align --set ON_ERROR_STOP=1 \
  --command "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public' AND tablename LIKE 'guacamole_%' ORDER BY tablename")"

if [ -z "$existing_tables" ]; then
  psql --no-psqlrc --set ON_ERROR_STOP=1 --file /bootstrap/guacamole-schema.sql
else
  expected_tables="$(printf '%s\n' "$readonly_schema_tables" | sed '/^$/d' | sort)"
  if [ "$existing_tables" != "$expected_tables" ]; then
    printf '%s\n' 'ERROR: partial or incompatible Guacamole schema detected; refusing to mutate it' >&2
    exit 1
  fi
fi

psql --no-psqlrc --set ON_ERROR_STOP=1 \
  --set vnc_password="$DESKTOP_VNC_PASSWORD" <<'SQL'
BEGIN;

-- Header authentication must map to one non-admin JDBC user. Remove the
-- schema's default administrator and any unexpected principal/permission.
DELETE FROM guacamole_entity
WHERE type = 'USER' AND name <> 'adpulse-desktop';

INSERT INTO guacamole_entity (name, type)
VALUES ('adpulse-desktop', 'USER')
ON CONFLICT (type, name) DO NOTHING;

INSERT INTO guacamole_user (entity_id, password_hash, password_date)
SELECT entity_id, decode(repeat('00', 32), 'hex'), CURRENT_TIMESTAMP
FROM guacamole_entity
WHERE name = 'adpulse-desktop' AND type = 'USER'
ON CONFLICT (entity_id) DO UPDATE SET disabled = FALSE, expired = FALSE;

DELETE FROM guacamole_system_permission;
DELETE FROM guacamole_user_permission;
DELETE FROM guacamole_user_group_permission;
DELETE FROM guacamole_connection_group_permission;
DELETE FROM guacamole_sharing_profile_permission;

-- This database is dedicated to the single production desktop. Reconcile it
-- to exactly one connection rather than silently retaining stale rows.
DELETE FROM guacamole_connection WHERE connection_name <> 'Vision Desktop';
DELETE FROM guacamole_connection
WHERE connection_name = 'Vision Desktop'
  AND connection_id <> (
    SELECT min(connection_id) FROM guacamole_connection
    WHERE connection_name = 'Vision Desktop'
  );

INSERT INTO guacamole_connection (connection_name, protocol)
SELECT 'Vision Desktop', 'vnc'
WHERE NOT EXISTS (
  SELECT 1 FROM guacamole_connection WHERE connection_name = 'Vision Desktop'
);

UPDATE guacamole_connection
SET parent_id = NULL,
    protocol = 'vnc',
    max_connections = NULL,
    max_connections_per_user = NULL,
    proxy_port = NULL,
    proxy_hostname = NULL,
    proxy_encryption_method = NULL
WHERE connection_name = 'Vision Desktop';

DELETE FROM guacamole_connection_parameter
WHERE connection_id = (
  SELECT connection_id FROM guacamole_connection
  WHERE connection_name = 'Vision Desktop'
);

INSERT INTO guacamole_connection_parameter
  (connection_id, parameter_name, parameter_value)
SELECT connection_id, parameter_name, parameter_value
FROM guacamole_connection
CROSS JOIN (VALUES
  ('hostname', '127.0.0.1'),
  ('port', '5900'),
  ('password', :'vnc_password'),
  ('width', '1366'),
  ('height', '768'),
  ('disable-display-resize', 'true'),
  ('read-only', 'false')
) AS parameters(parameter_name, parameter_value)
WHERE connection_name = 'Vision Desktop';

DELETE FROM guacamole_connection_permission;
INSERT INTO guacamole_connection_permission (entity_id, connection_id, permission)
SELECT entity.entity_id, connection.connection_id, 'READ'
FROM guacamole_entity AS entity
CROSS JOIN guacamole_connection AS connection
WHERE entity.name = 'adpulse-desktop'
  AND entity.type = 'USER'
  AND connection.connection_name = 'Vision Desktop';

DO $$
BEGIN
  IF (SELECT count(*) FROM guacamole_connection) <> 1 THEN
    RAISE EXCEPTION 'expected exactly one Guacamole connection';
  END IF;
  IF (SELECT count(*) FROM guacamole_connection_permission) <> 1 OR EXISTS (
    SELECT 1 FROM guacamole_connection_permission WHERE permission <> 'READ'
  ) THEN
    RAISE EXCEPTION 'expected exactly one READ connection permission';
  END IF;
  IF EXISTS (SELECT 1 FROM guacamole_system_permission) THEN
    RAISE EXCEPTION 'system permissions are forbidden for desktop principal';
  END IF;
END
$$;

COMMIT;
SQL
