#!/bin/sh
set -e

TEMPLATES_DIR=/etc/nginx/templates
CONF_DIR=/etc/nginx/conf.d

# --- Defaults for variables rendered into the templates ---

# PUBLIC_BASE_URL: externally visible site origin (scheme + host + PUBLIC port),
# e.g. https://ib.bithollow.org:65533. It is the target of the HTTP->HTTPS 301
# redirect. The container HTTPS port often differs from the public port behind
# a firewall/NAT mapping (e.g. public 65533 -> host 10443), so the redirect
# must NOT be derived from $host:$HTTPS_PORT.
# Default (when unset/empty): https://$host — the single quotes keep $host a
# literal so nginx resolves it from the Host header at request time; this is
# only correct when the public HTTPS port is the standard 443.
if [ -z "${PUBLIC_BASE_URL:-}" ]; then
    PUBLIC_BASE_URL='https://$host'
fi
export PUBLIC_BASE_URL

# HSTS max-age in seconds (see the header comment in https-server.conf.template
# for why `preload` must never be added on non-standard public ports).
HSTS_MAX_AGE="${HSTS_MAX_AGE:-31536000}"
export HSTS_MAX_AGE

# Clean up stale confs (so removed variables don't linger)
rm -f "$CONF_DIR"/*.conf

if [ "$ENABLE_HTTPS" = "true" ]; then
    echo "HTTPS enabled — rendering https.conf + http-redirect.conf"
    # PUBLIC_BASE_URL is already exported above and listed here for rendering
    # even though https-server.conf.template does not use it yet — reserving it
    # so future template usage needs no entrypoint change.
    envsubst '${HTTPS_PORT} ${SERVER_NAME} ${HSTS_MAX_AGE} ${PUBLIC_BASE_URL}' \
        < "$TEMPLATES_DIR/https-server.conf.template" \
        > "$CONF_DIR/https.conf"
    # $request_uri is deliberately NOT in the substitution list so it stays an
    # nginx runtime variable in the rendered config.
    envsubst '${HTTP_PORT} ${SERVER_NAME} ${PUBLIC_BASE_URL}' \
        < "$TEMPLATES_DIR/http-redirect.conf.template" \
        > "$CONF_DIR/http.conf"
else
    echo "HTTPS disabled — rendering http.conf only"
    envsubst '${HTTP_PORT} ${SERVER_NAME}' \
        < "$TEMPLATES_DIR/http-server.conf.template" \
        > "$CONF_DIR/http.conf"
fi

exec nginx -g "daemon off;"
