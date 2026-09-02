#!/bin/zsh

set -u
umask 077

if [[ -z "${HOME:-}" || "${HOME}" != /* || "${HOME}" == "/" ]]; then
    printf 'HOME must be a non-root absolute user directory\n' >&2
    exit 2
fi

readonly SCRIPT_DIR="${0:A:h}"
readonly CLOUD_OAM_DIR="${SCRIPT_DIR:h}"
readonly PROJECT_ROOT="${CLOUD_OAM_DIR:h}"
readonly USER_HOME="${HOME:A}"
readonly CONFIG_DIR="${USER_HOME}/.config/rsc-edge-sync"
readonly CONFIG_FILE="${CONFIG_DIR}/env"
readonly LAST_FULL_DATE_FILE="${CONFIG_DIR}/last-completed-full-sync-date"
readonly PYTHON="${RSC_EDGE_PYTHON:-/usr/bin/python3}"
readonly SYNC_SCRIPT="${SCRIPT_DIR}/oam_edge_sync.py"
readonly LOG_DIR="${USER_HOME}/Library/Logs/RSC"
readonly LOG_FILE="${LOG_DIR}/oam-edge-sync.log"
readonly MAX_LOG_BYTES=5242880

mkdir -p "${LOG_DIR}"
chmod 700 "${LOG_DIR}"

if [[ -f "${LOG_FILE}" ]] && (( $(stat -f %z "${LOG_FILE}") >= MAX_LOG_BYTES )); then
    rm -f "${LOG_FILE}.3"
    [[ -f "${LOG_FILE}.2" ]] && mv "${LOG_FILE}.2" "${LOG_FILE}.3"
    [[ -f "${LOG_FILE}.1" ]] && mv "${LOG_FILE}.1" "${LOG_FILE}.2"
    mv "${LOG_FILE}" "${LOG_FILE}.1"
fi

{
    printf '\n[%s] scheduled sync start\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    if [[ ! -r "${CONFIG_FILE}" ]]; then
        printf 'configuration file is unavailable: %s\n' "${CONFIG_FILE}"
        exit 2
    fi
    set -a
    source "${CONFIG_FILE}"
    set +a
    export GLOBAL_SESSION_IMPORT_WORK=0
    export GLOBAL_SESSION_OUTPUT_DIR="${GLOBAL_SESSION_OUTPUT_DIR:-${CONFIG_DIR}/health}"
    mkdir -p "${GLOBAL_SESSION_OUTPUT_DIR}"
    if [[ ! -x "${PYTHON}" ]]; then
        printf 'python interpreter is unavailable: %s\n' "${PYTHON}"
        exit 2
    fi
    full_sync_date="$(date '+%Y-%m-%d')"
    force_full_args=()
    if [[ ! -r "${LAST_FULL_DATE_FILE}" ]] || \
       [[ "$(<"${LAST_FULL_DATE_FILE}")" != "${full_sync_date}" ]]; then
        force_full_args=(--force-full)
    fi
    "${PYTHON}" "${SYNC_SCRIPT}" \
        --entity all \
        --batch-size 300 \
        "${force_full_args[@]}" \
        --scheduled
    base_exit_code=$?
    if (( base_exit_code != 0 )); then
        printf '[%s] scheduled base sync exit=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "${base_exit_code}"
        exit "${base_exit_code}"
    fi
    "${PYTHON}" "${SYNC_SCRIPT}" \
        --entity work-orders \
        --work-order-days 30 \
        --batch-size 300 \
        "${force_full_args[@]}" \
        --scheduled
    exit_code=$?
    if (( exit_code == 0 && ${#force_full_args[@]} > 0 )); then
        full_marker_tmp="${LAST_FULL_DATE_FILE}.tmp.$$"
        printf '%s\n' "${full_sync_date}" >"${full_marker_tmp}"
        chmod 600 "${full_marker_tmp}"
        mv "${full_marker_tmp}" "${LAST_FULL_DATE_FILE}"
    fi
    printf '[%s] scheduled sync exit=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "${exit_code}"
    exit "${exit_code}"
} >>"${LOG_FILE}" 2>&1
