#!/usr/bin/env sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
release_root=${1:-/var/www/bizorch/releases}
release_id=${2:?usage: publish-frontend.sh RELEASE_ROOT RELEASE_ID}
temporary_output=$(mktemp -d)
trap 'rm -rf "$temporary_output"' EXIT

docker buildx build \
    --platform linux/amd64 \
    --target artifact \
    --output "type=local,dest=${temporary_output}" \
    -f "$repository_root/frontend/Dockerfile" \
    "$repository_root/frontend"

install -d -m 0755 "$release_root/$release_id"
cp -R "$temporary_output/dist/." "$release_root/$release_id/"
ln -sfn "$release_root/$release_id" "$(dirname "$release_root")/current"

echo "published frontend release ${release_id}"
