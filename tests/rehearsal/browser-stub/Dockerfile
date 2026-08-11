ARG BASE_IMAGE=node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0
FROM ${BASE_IMAGE}

COPY tests/rehearsal/browser-stub/server.mjs /app/rehearsal-server.mjs
COPY tests/rehearsal/browser-stub/telegram-server.mjs /app/rehearsal-telegram-server.mjs
COPY tests/rehearsal/browser-stub/entrypoint.sh /usr/local/bin/rehearsal-entrypoint
COPY tests/rehearsal/browser-stub/healthcheck.sh /usr/local/bin/healthcheck.sh
RUN chmod 0555 /usr/local/bin/rehearsal-entrypoint /usr/local/bin/healthcheck.sh \
    && chmod 0444 /app/rehearsal-server.mjs /app/rehearsal-telegram-server.mjs

ENTRYPOINT ["/usr/local/bin/rehearsal-entrypoint"]
