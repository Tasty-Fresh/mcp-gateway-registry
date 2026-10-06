#!/usr/bin/env bash
#
# prepare-log-dirs.sh (Issue #987)
#
# Creates and chowns the host log directory used by AI Registry containers.
# The registry, auth-server, and mcpgw containers bind-mount
# /var/log/containers/ai-registry/ from the host so customer Splunk
# forwarders can ingest the .log files directly.
#
# Containers run as uid 1000 (appuser), so the host directory must be owned
# by 1000:1000 for the non-root container user to write into it.
#
# Safe to run multiple times. Idempotent.

set -euo pipefail

LOG_BASE="${APP_LOG_DIR:-/var/log/containers/ai-registry}"
OWNER_UID="${LOG_DIR_OWNER_UID:-1000}"
OWNER_GID="${LOG_DIR_OWNER_GID:-1000}"
DIR_MODE="${LOG_DIR_MODE:-0750}"

echo "Preparing host log directory for AI Registry..."
echo "  Path:  ${LOG_BASE}"
echo "  Owner: ${OWNER_UID}:${OWNER_GID}"
echo "  Mode:  ${DIR_MODE}"

# Ownership must map to the container uid/gid (1000:1000), and it must not
# require sudo. For rootless Podman, `podman unshare` runs the command in the
# user namespace where the caller maps to root, so mkdir/chown/chmod can operate
# on root-owned host paths without sudo. For a rootful deployment (running as
# root) the plain commands already have permission. For a non-root non-Podman
# deployment (e.g. rootful Docker), fall back to sudo as before.
if command -v podman &> /dev/null && [ "$(id -u)" -ne 0 ]; then
    echo "Using rootless Podman (podman unshare) to set ownership without sudo..."
    if [ ! -d "${LOG_BASE}" ]; then
        echo "Creating ${LOG_BASE} (via podman unshare)"
        podman unshare mkdir -p "${LOG_BASE}"
    fi
    podman unshare chown -R "${OWNER_UID}:${OWNER_GID}" "${LOG_BASE}"
    podman unshare chmod "${DIR_MODE}" "${LOG_BASE}"
elif [ "$(id -u)" -eq 0 ]; then
    echo "Running as root; setting ownership directly..."
    mkdir -p "${LOG_BASE}"
    chown -R "${OWNER_UID}:${OWNER_GID}" "${LOG_BASE}"
    chmod "${DIR_MODE}" "${LOG_BASE}"
else
    echo "Not rootless Podman and not root; using sudo for ownership..."
    if [ ! -d "${LOG_BASE}" ]; then
        echo "Creating ${LOG_BASE} (via sudo)"
        sudo mkdir -p "${LOG_BASE}"
    fi
    sudo chown -R "${OWNER_UID}:${OWNER_GID}" "${LOG_BASE}"
    sudo chmod "${DIR_MODE}" "${LOG_BASE}"
fi

echo "OK: ${LOG_BASE} prepared"
ls -ld "${LOG_BASE}"
