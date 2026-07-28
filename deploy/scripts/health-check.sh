#!/usr/bin/env sh
set -eu

api_port=${1:-18000}
health_url="http://127.0.0.1:${api_port}/api/v1/health"

if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required for the host health check" >&2
    exit 1
fi

curl --fail --silent --show-error --max-time 10 "$health_url"
echo
