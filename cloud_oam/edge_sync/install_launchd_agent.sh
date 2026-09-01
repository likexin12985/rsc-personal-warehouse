#!/bin/zsh

set -euo pipefail
umask 077

if [[ -z "${HOME:-}" || "${HOME}" != /* || "${HOME}" == "/" ]]; then
    printf 'HOME must be a non-root absolute user directory\n' >&2
    exit 2
fi

readonly LABEL="cn.rsc.oam-edge-sync"
readonly SCRIPT_DIR="${0:A:h}"
readonly PROJECT_ROOT="${SCRIPT_DIR:h:h}"
readonly USER_HOME="${HOME:A}"
readonly RUNTIME_ROOT="${USER_HOME}/Library/Application Support/RSC/oam-edge-sync/runtime"
readonly RUNTIME_EDGE_DIR="${RUNTIME_ROOT}/cloud_oam/edge_sync"
readonly RUNTIME_WORK_DIR="${RUNTIME_ROOT}/work"
readonly CONFIG_DIR="${USER_HOME}/.config/rsc-edge-sync"
readonly CONFIG_FILE="${CONFIG_DIR}/env"
readonly SESSION_FILE="${CONFIG_DIR}/oam_session.json"
readonly PROJECT_SESSION_FILE="${PROJECT_ROOT}/work/oam_session.json"
readonly AGENT_DIR="${USER_HOME}/Library/LaunchAgents"
readonly AGENT_FILE="${AGENT_DIR}/${LABEL}.plist"
readonly LOG_DIR="${USER_HOME}/Library/Logs/RSC"
readonly DOMAIN="gui/$(id -u)"

require_file() {
    if [[ ! -f "$1" ]]; then
        printf 'required file is unavailable: %s\n' "$1" >&2
        exit 2
    fi
}

configure_shared_session() {
    mkdir -p "${CONFIG_DIR}"
    chmod 700 "${CONFIG_DIR}"

    if [[ -L "${PROJECT_SESSION_FILE}" ]]; then
        if [[ "${PROJECT_SESSION_FILE:A}" != "${SESSION_FILE:A}" ]]; then
            printf 'project session symlink points to an unexpected file: %s\n' "${PROJECT_SESSION_FILE}" >&2
            exit 2
        fi
    elif [[ -f "${SESSION_FILE}" ]]; then
        if [[ ! -f "${PROJECT_SESSION_FILE}" ]] || ! cmp -s "${PROJECT_SESSION_FILE}" "${SESSION_FILE}"; then
            printf 'shared OAM session files differ; refusing to choose one automatically\n' >&2
            exit 2
        fi
        rm "${PROJECT_SESSION_FILE}"
        ln -s "${SESSION_FILE}" "${PROJECT_SESSION_FILE}"
    elif [[ -f "${PROJECT_SESSION_FILE}" ]]; then
        mv "${PROJECT_SESSION_FILE}" "${SESSION_FILE}"
        chmod 600 "${SESSION_FILE}"
        ln -s "${SESSION_FILE}" "${PROJECT_SESSION_FILE}"
    else
        printf 'shared OAM session is unavailable: %s\n' "${PROJECT_SESSION_FILE}" >&2
        exit 2
    fi

    chmod 600 "${SESSION_FILE}"
    local env_tmp="${CONFIG_FILE}.tmp.$$"
    awk -v value="OAM_SESSION_FILE=${SESSION_FILE}" '
        BEGIN { replaced = 0 }
        /^OAM_SESSION_FILE=/ {
            if (!replaced) {
                print value
                replaced = 1
            }
            next
        }
        { print }
        END {
            if (!replaced) print value
        }
    ' "${CONFIG_FILE}" >"${env_tmp}"
    chmod 600 "${env_tmp}"
    mv "${env_tmp}" "${CONFIG_FILE}"
}

install_runtime() {
    mkdir -p \
        "${RUNTIME_EDGE_DIR}" \
        "${RUNTIME_WORK_DIR}/inventory_query_portal" \
        "${RUNTIME_WORK_DIR}/rsc_ningbo_group_bot"

    install -m 700 "${SCRIPT_DIR}/run_scheduled_sync.sh" "${RUNTIME_EDGE_DIR}/run_scheduled_sync.sh"
    install -m 700 "${SCRIPT_DIR}/run_launchd_sync.sh" "${RUNTIME_EDGE_DIR}/run_launchd_sync.sh"
    install -m 600 "${SCRIPT_DIR}/oam_edge_sync.py" "${RUNTIME_EDGE_DIR}/oam_edge_sync.py"
    install -m 600 "${PROJECT_ROOT}/work/oam_shared_session.py" "${RUNTIME_WORK_DIR}/oam_shared_session.py"
    install -m 600 "${PROJECT_ROOT}/work/global_business_session_health.py" "${RUNTIME_WORK_DIR}/global_business_session_health.py"
    install -m 600 "${PROJECT_ROOT}/work/inventory_query_portal/oam_read_client.py" "${RUNTIME_WORK_DIR}/inventory_query_portal/oam_read_client.py"
    install -m 600 "${PROJECT_ROOT}/work/inventory_query_portal/query_oam_flows.py" "${RUNTIME_WORK_DIR}/inventory_query_portal/query_oam_flows.py"
    install -m 600 "${PROJECT_ROOT}/work/inventory_query_portal/query_oam_work_orders.py" "${RUNTIME_WORK_DIR}/inventory_query_portal/query_oam_work_orders.py"
    install -m 600 "${PROJECT_ROOT}/work/rsc_ningbo_group_bot/oam_gateway.py" "${RUNTIME_WORK_DIR}/rsc_ningbo_group_bot/oam_gateway.py"
}

install_agent() {
    mkdir -p "${AGENT_DIR}" "${LOG_DIR}"
    chmod 700 "${AGENT_DIR}" "${LOG_DIR}"

    local agent_tmp="${AGENT_FILE}.tmp.$$"
    install -m 600 "${SCRIPT_DIR}/${LABEL}.plist" "${agent_tmp}"
    plutil -replace ProgramArguments.0 -string \
        "${RUNTIME_EDGE_DIR}/run_launchd_sync.sh" "${agent_tmp}"
    plutil -replace WorkingDirectory -string "${RUNTIME_ROOT}" "${agent_tmp}"
    plutil -replace EnvironmentVariables.HOME -string "${USER_HOME}" "${agent_tmp}"
    plutil -replace StandardOutPath -string \
        "${LOG_DIR}/oam-edge-sync-launchd.stdout.log" "${agent_tmp}"
    plutil -replace StandardErrorPath -string \
        "${LOG_DIR}/oam-edge-sync-launchd.stderr.log" "${agent_tmp}"
    plutil -lint "${agent_tmp}" >/dev/null
    mv "${agent_tmp}" "${AGENT_FILE}"

    launchctl bootout "${DOMAIN}/${LABEL}" >/dev/null 2>&1 || true
    launchctl bootstrap "${DOMAIN}" "${AGENT_FILE}"
}

require_file "${CONFIG_FILE}"
require_file "${PROJECT_SESSION_FILE}"
configure_shared_session
install_runtime
install_agent

printf 'installed %s\n' "${LABEL}"
printf 'runtime %s\n' "${RUNTIME_ROOT}"
printf 'session %s\n' "${SESSION_FILE}"
