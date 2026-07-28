#!/usr/bin/env sh
set -eu

deployment_root=${1:-/opt/bizorch}

case "$deployment_root" in
    /|/*) ;;
    *)
        echo "deployment root must be an absolute path" >&2
        exit 1
        ;;
esac

if [ "$deployment_root" = "/" ]; then
    echo "deployment root cannot be /" >&2
    exit 1
fi

install -d -m 0750 "$deployment_root"
install -d -m 0750 "$deployment_root/data/chroma"
install -d -m 0750 "$deployment_root/data/checkpoints"
install -d -m 0750 "$deployment_root/data/uploads"
install -d -m 0750 "$deployment_root/logs"
install -d -m 0750 "$deployment_root/backups"
install -d -m 0750 "$deployment_root/releases"

# The API image runs as UID/GID 10001; do not use chmod 777 for bind mounts.
chown -R 10001:10001 "$deployment_root/data" "$deployment_root/logs"

echo "host directories initialized under $deployment_root"
