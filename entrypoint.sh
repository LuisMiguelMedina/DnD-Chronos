#!/bin/sh
set -eu
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chmod 777 /data
fi
exec "$@"
