#!/bin/sh
set -eu
umask 077

case "$APP_HOST" in
    ''|*[!A-Za-z0-9.-]*) echo 'APP_HOST must be an IPv4 address or DNS name' >&2; exit 1 ;;
esac
case "$APP_PORT" in
    ''|*[!0-9]*) echo 'APP_PORT must be a TCP port number' >&2; exit 1 ;;
esac
if [ "$APP_PORT" -lt 1 ] || [ "$APP_PORT" -gt 65535 ]; then
    echo 'APP_PORT must be between 1 and 65535' >&2
    exit 1
fi

certs=/etc/nginx/certs
mkdir -p "$certs"
san=DNS
check=-checkhost
if printf '%s' "$APP_HOST" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
    san=IP
    check=-checkip
fi
if [ -s "$certs/server.crt" ] && [ -s "$certs/server.key" ] && [ ! -s "$certs/root.crt" ]; then
    # Support a user-provided certificate on the very first startup as well.
    openssl x509 -in "$certs/server.crt" -checkend 0 -noout >/dev/null
    openssl x509 -in "$certs/server.crt" "$check" "$APP_HOST" -noout >/dev/null
    exit 0
fi
if { [ -s "$certs/root.crt" ] && [ ! -s "$certs/root.key" ]; } || \
    { [ -s "$certs/root.key" ] && [ ! -s "$certs/root.crt" ]; }; then
    echo 'Incomplete local CA; restore its matching certificate and key' >&2
    exit 1
fi
fresh_ca=false
if [ ! -s "$certs/root.key" ] || [ ! -s "$certs/root.crt" ]; then
    openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 \
        -subj '/CN=IPtable local CA' \
        -addext 'basicConstraints=critical,CA:TRUE' \
        -addext 'keyUsage=critical,keyCertSign,cRLSign' \
        -keyout "$certs/root.key" -out "$certs/root.crt" 2>/dev/null
    fresh_ca=true
fi

if [ -s "$certs/server.crt" ] && [ "$fresh_ca" = false ] && \
    ! openssl verify -no_check_time -no-CApath -no-CAstore -CAfile "$certs/root.crt" \
        "$certs/server.crt" >/dev/null 2>&1; then
    # An externally supplied certificate is never silently replaced by our CA.
    test -s "$certs/server.key"
    openssl x509 -in "$certs/server.crt" -checkend 0 -noout >/dev/null
    openssl x509 -in "$certs/server.crt" "$check" "$APP_HOST" -noout >/dev/null
    exit 0
fi
if [ "$fresh_ca" = true ] || [ ! -s "$certs/server.key" ] || \
    ! openssl x509 -in "$certs/server.crt" -checkend 2592000 -noout >/dev/null 2>&1 || \
    ! openssl x509 -in "$certs/server.crt" "$check" "$APP_HOST" -noout >/dev/null 2>&1; then
    openssl req -new -newkey rsa:3072 -nodes -sha256 -subj "/CN=$APP_HOST" \
        -keyout "$certs/server.key" -out "$certs/server.csr" 2>/dev/null
    printf 'subjectAltName=%s:%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n' \
        "$san" "$APP_HOST" > "$certs/server.ext"
    openssl x509 -req -in "$certs/server.csr" -CA "$certs/root.crt" -CAkey "$certs/root.key" \
        -CAcreateserial -out "$certs/server.crt" -days 365 -sha256 -extfile "$certs/server.ext" 2>/dev/null
    rm -f "$certs/server.csr" "$certs/server.ext"
fi
