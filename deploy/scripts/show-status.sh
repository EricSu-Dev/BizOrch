#!/usr/bin/env sh
set -eu

deployment_root=${1:-/opt/bizorch}
compose_file="$deployment_root/compose.yaml"
runtime_env_file="$deployment_root/.env"

if [ ! -f "$compose_file" ] || [ ! -f "$runtime_env_file" ]; then
    echo "missing compose.yaml or .env under deployment root" >&2
    exit 1
fi

docker compose --env-file "$runtime_env_file" -f "$compose_file" ps
docker stats --no-stream
