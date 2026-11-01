FROM postgres:16-alpine

# su-exec provides the same exec/UID/GID transition without a bundled Go runtime.
# hadolint ignore=DL3018
RUN apk add --no-cache su-exec \
    && grep -q 'exec gosu postgres' /usr/local/bin/docker-entrypoint.sh \
    && sed -i 's/exec gosu postgres/exec su-exec postgres/' /usr/local/bin/docker-entrypoint.sh \
    && rm /usr/local/bin/gosu

USER postgres
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}"
