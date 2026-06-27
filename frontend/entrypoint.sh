#!/bin/sh
set -e

rm -rf /usr/share/nginx/html/*
cp -r /app/dist/* /usr/share/nginx/html/

exec nginx -g "daemon off;"
