#!/usr/bin/env sh
set -eu

runtime_env_file=${1:-/opt/bizorch/.env}

if [ ! -f "$runtime_env_file" ]; then
    echo "missing runtime env file" >&2
    exit 1
fi

required_keys='BIZORCH_DATA_DIR BIZORCH_LOG_DIR BIZORCH_API_IMAGE BIZORCH_ENTERPRISE_IMAGE BIZORCH_MCP_IMAGE BIZORCH_DATABASE_URL ENTERPRISE_DATABASE_URL ENTERPRISE_INTERNAL_TOKEN BIZORCH_MCP_READ_TOKEN BIZORCH_MCP_ACTION_GATEWAY_TOKEN DEEPSEEK_API_KEY DASHSCOPE_API_KEY BIZORCH_OSS_BUCKET_NAME BIZORCH_OSS_ENDPOINT BIZORCH_OSS_ACCESS_KEY_ID BIZORCH_OSS_ACCESS_KEY_SECRET'

file_mode=$(stat -c '%a' "$runtime_env_file")
if [ "$file_mode" != "600" ]; then
    echo "runtime env file mode must be 600" >&2
    exit 1
fi

missing=0
for required_key in $required_keys; do
    occurrence_count=$(awk -F= -v key="$required_key" '$1 == key { count += 1 } END { print count + 0 }' "$runtime_env_file")
    if [ "$occurrence_count" -ne 1 ]; then
        echo "configuration must contain exactly one value for: ${required_key}" >&2
        missing=1
        continue
    fi
    configured_value=$(sed -n "s/^${required_key}=//p" "$runtime_env_file" | head -n 1)
    case "$configured_value" in
        ''|*replace-me*|*replace-with-*|*change-me*)
            echo "missing required configuration: ${required_key}" >&2
            missing=1
            ;;
    esac
done

for path_key in BIZORCH_DATA_DIR BIZORCH_LOG_DIR; do
    path_value=$(sed -n "s/^${path_key}=//p" "$runtime_env_file" | head -n 1)
    case "$path_value" in
        /*) ;;
        *)
            echo "configuration must use an absolute Linux path: ${path_key}" >&2
            missing=1
            ;;
    esac
done

for image_key in BIZORCH_API_IMAGE BIZORCH_ENTERPRISE_IMAGE BIZORCH_MCP_IMAGE; do
    image_value=$(sed -n "s/^${image_key}=//p" "$runtime_env_file" | head -n 1)
    case "$image_value" in
        *:latest|latest)
            echo "deployment image must not use latest: ${image_key}" >&2
            missing=1
            ;;
    esac
done

internal_token=$(sed -n 's/^ENTERPRISE_INTERNAL_TOKEN=//p' "$runtime_env_file" | head -n 1)
if [ "${#internal_token}" -lt 32 ]; then
    echo "ENTERPRISE_INTERNAL_TOKEN must contain at least 32 characters" >&2
    missing=1
fi

read_token=$(sed -n 's/^BIZORCH_MCP_READ_TOKEN=//p' "$runtime_env_file" | head -n 1)
gateway_token=$(sed -n 's/^BIZORCH_MCP_ACTION_GATEWAY_TOKEN=//p' "$runtime_env_file" | head -n 1)
if [ ${#read_token} -lt 32 ] || [ ${#gateway_token} -lt 32 ] || [ "$read_token" = "$gateway_token" ]; then
    echo "MCP read and Action Gateway tokens must be distinct and at least 32 characters" >&2
    exit 1
fi

if [ "$missing" -ne 0 ]; then
    exit 1
fi

echo "runtime env validation passed"
