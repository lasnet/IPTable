#!/bin/sh
set -eu

fail() { echo "HTTPS configuration: $1" >&2; exit 1; }
body_limit=${MAX_REQUEST_BODY_BYTES:-3145728}
case "$body_limit" in ''|*[!0-9]*) fail 'MAX_REQUEST_BODY_BYTES must be an integer' ;; esac
test "$body_limit" -ge 1024 && test "$body_limit" -le 67108864 || fail 'MAX_REQUEST_BODY_BYTES is out of range'
export MAX_REQUEST_BODY_BYTES="$body_limit"
case "${TLS_SERVER_NAME:-}" in
    ''|*[!A-Za-z0-9.-]*) fail 'TLS_SERVER_NAME must be a DNS name or IPv4 address' ;;
esac
# Restrict values interpolated into Nginx configuration to a single HTTPS origin.
case "${APP_PUBLIC_URL:-}" in
    https://*) origin=${APP_PUBLIC_URL#https://} ;;
    *) fail 'APP_PUBLIC_URL must start with https://' ;;
esac
case "$origin" in
    ''|*[!A-Za-z0-9.:-]*) fail 'APP_PUBLIC_URL must contain only a hostname and optional port' ;;
esac
host=${origin%%:*}
test "$host" = "$TLS_SERVER_NAME" || fail 'APP_PUBLIC_URL hostname must match TLS_SERVER_NAME'
if [ "$origin" != "$host" ]; then
    port=${origin#*:}
    case "$port" in ''|*[!0-9]*) fail 'Invalid HTTPS port in APP_PUBLIC_URL' ;; esac
    test "$port" -ge 1 && test "$port" -le 65535 || fail 'Invalid HTTPS port in APP_PUBLIC_URL'
fi
cert=/etc/nginx/certs/server.crt
key=/etc/nginx/certs/server.key
test -r "$cert" && test -s "$cert" || fail 'Certificate is missing or unreadable by the nginx user'
test -r "$key" && test -s "$key" || fail 'Private key is missing or unreadable by the nginx user'
openssl x509 -in "$cert" -checkend 0 -noout >/dev/null || fail 'Certificate is invalid or expired'
check=-checkhost
if printf '%s' "$host" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then check=-checkip; fi
openssl x509 -in "$cert" "$check" "$host" -noout >/dev/null || fail 'Certificate does not cover TLS_SERVER_NAME'
cert_public=$(openssl x509 -in "$cert" -pubkey -noout) || fail 'Cannot read certificate public key'
key_public=$(openssl pkey -in "$key" -passin pass: -pubout 2>/dev/null) || fail 'Private key must be valid and unencrypted'
test "$cert_public" = "$key_public" || fail 'Certificate and private key do not match'
