FROM nginx:stable-alpine

# hadolint ignore=DL3018
RUN apk upgrade --no-cache && apk add --no-cache openssl
COPY default.conf.template /etc/nginx/templates/default.conf.template
COPY 15-local-tls.sh /docker-entrypoint.d/15-local-tls.sh
RUN chmod 755 /docker-entrypoint.d/15-local-tls.sh \
    && mkdir -p /etc/nginx/certs \
    && chown -R nginx:nginx /etc/nginx/certs /etc/nginx/conf.d /var/cache/nginx \
    && sed -i 's|/var/run/nginx.pid|/tmp/nginx.pid|; s|/run/nginx.pid|/tmp/nginx.pid|; /^user /d' /etc/nginx/nginx.conf
ENV APP_HOST=localhost APP_PORT=8000
USER nginx
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8081/health
