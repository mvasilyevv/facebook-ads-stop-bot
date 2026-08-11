#!/bin/sh
set -eu

if [ "${REHEARSAL_ROLE:-}" = "telegram" ]; then
  exec node /app/rehearsal-telegram-server.mjs
fi

if [ -n "${GRPC_PORT:-}" ]; then
  exec node /app/rehearsal-server.mjs
fi

exec node -e 'setInterval(() => {}, 2147483647)'
