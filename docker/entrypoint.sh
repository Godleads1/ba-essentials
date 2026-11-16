#!/bin/sh
set -eu

# The ClamAV daemon runs as a separate private service, so give clamdscan a
# TCP configuration and stream files to it.
if [ -n "${CLAMD_HOST:-}" ]; then
    {
        printf 'TCPSocket %s\n' "${CLAMD_PORT:-3310}"
        printf 'TCPAddr %s\n' "$CLAMD_HOST"
    } > /tmp/clamd.conf
    export CLAMD_CONFIG_FILE=/tmp/clamd.conf
fi

if [ -n "${PRIVATE_STORAGE_ROOT:-}" ]; then
    mkdir -p "$PRIVATE_STORAGE_ROOT"
    if [ "$(id -u)" = "0" ]; then
        # Render mounts persistent disks owned by root. The app user needs to
        # create and read files below the mount.
        chown app:app "$PRIVATE_STORAGE_ROOT" 2>/dev/null || true
    fi
fi

if [ "$(id -u)" = "0" ]; then
    exec gosu app "$@"
fi

exec "$@"
