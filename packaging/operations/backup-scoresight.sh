#!/usr/bin/env bash
set -euo pipefail

data_dir="${SCORESIGHT_DATA_DIR:-/var/lib/docker/volumes/scoresight_scoresight-data/_data}"
backup_dir="${SCORESIGHT_BACKUP_DIR:-/var/backups/scoresight}"
now="$(date -u +%Y%m%dT%H%M%SZ)"

[[ "$data_dir" = /* && "$backup_dir" = /* ]] || { echo "paths must be absolute" >&2; exit 64; }
[[ -d "$data_dir" ]] || { echo "data directory does not exist: $data_dir" >&2; exit 66; }
install -d -m 0700 "$backup_dir/daily" "$backup_dir/weekly"
tar --create --gzip --file "$backup_dir/daily/scoresight-${now}.tar.gz" -C "$data_dir" .
find "$backup_dir/daily" -type f -name 'scoresight-*.tar.gz' -mtime +7 -delete

if [[ "$(date -u +%u)" == "7" ]]; then
  cp "$backup_dir/daily/scoresight-${now}.tar.gz" "$backup_dir/weekly/"
  find "$backup_dir/weekly" -type f -name 'scoresight-*.tar.gz' -mtime +28 -delete
fi

