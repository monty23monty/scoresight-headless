#!/bin/sh
set -eu

config_path="${SCORESIGHT_DATA_DIR:-/var/lib/scoresight}/config-v1.json"
if [ ! -f "$config_path" ]; then
    cp /opt/scoresight/config-v1.production.json "$config_path"
fi

exec "$@"

