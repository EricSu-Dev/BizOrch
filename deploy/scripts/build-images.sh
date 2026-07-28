#!/usr/bin/env sh
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
image_tag=${1:?usage: build-images.sh IMAGE_TAG}

docker build --platform linux/amd64 -f "$repository_root/backend/Dockerfile" -t "bizorch-api:${image_tag}" "$repository_root"
docker build --platform linux/amd64 -f "$repository_root/enterprise_system/Dockerfile" -t "bizorch-enterprise:${image_tag}" "$repository_root"
docker build --platform linux/amd64 -f "$repository_root/enterprise_ops_mcp/Dockerfile" -t "bizorch-enterprise-mcp:${image_tag}" "$repository_root"

echo "built BizOrch images with tag ${image_tag}"
