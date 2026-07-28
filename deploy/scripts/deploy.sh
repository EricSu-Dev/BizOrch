#!/usr/bin/env sh
set -eu

deployment_root=${1:-/opt/bizorch}
compose_file="$deployment_root/compose.yaml"
runtime_env_file="$deployment_root/.env"

if [ ! -f "$compose_file" ] || [ ! -f "$runtime_env_file" ]; then
    echo "missing compose.yaml or .env under deployment root" >&2
    exit 1
fi

"$(dirname "$0")/check-runtime-env.sh" "$runtime_env_file"
# Private-registry deployments are pulled when reachable. Images transferred with
# docker save/load remain usable when no registry is configured or reachable.
docker compose --env-file "$runtime_env_file" -f "$compose_file" pull --ignore-pull-failures
docker compose --env-file "$runtime_env_file" -f "$compose_file" up -d --remove-orphans --no-build
docker compose --env-file "$runtime_env_file" -f "$compose_file" ps
