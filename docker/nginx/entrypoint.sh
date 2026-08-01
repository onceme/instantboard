#!/bin/sh
set -e

TEMPLATES_DIR=/etc/nginx/templates
CONF_DIR=/etc/nginx/conf.d

# Clean up stale confs (so removed variables don't linger)
rm -f "$CONF_DIR"/*.conf

if [ "$ENABLE_HTTPS" = "true" ]; then
    echo "HTTPS enabled — rendering https.conf + http-redirect.conf"
    envsubst '${HTTPS_PORT} ${SERVER_NAME}' \
        < "$TEMPLATES_DIR/https-server.conf.template" \
        > "$CONF_DIR/https.conf"
    envsubst '${HTTP_PORT} ${SERVER_NAME} ${HTTPS_PORT}' \
        < "$TEMPLATES_DIR/http-redirect.conf.template" \
        > "$CONF_DIR/http.conf"
else
    echo "HTTPS disabled — rendering http.conf only"
    envsubst '${HTTP_PORT} ${SERVER_NAME}' \
        < "$TEMPLATES_DIR/http-server.conf.template" \
        > "$CONF_DIR/http.conf"
fi

exec nginx -g "daemon off;"
