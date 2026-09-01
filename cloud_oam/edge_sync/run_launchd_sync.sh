#!/bin/zsh

set -u
umask 077

if [[ -z "${HOME:-}" || "${HOME}" != /* || "${HOME}" == "/" ]]; then
    printf 'HOME must be a non-root absolute user directory\n' >&2
    exit 2
fi

readonly SCRIPT_DIR="${0:A:h}"
readonly SYNC_SCRIPT="${SCRIPT_DIR}/run_scheduled_sync.sh"
readonly USER_HOME="${HOME:A}"
readonly CONFIG_DIR="${USER_HOME}/.config/rsc-edge-sync"
readonly LOG_FILE="${USER_HOME}/Library/Logs/RSC/oam-edge-sync.log"
readonly STATUS_FILE="${CONFIG_DIR}/launchd-status.env"
readonly FAILURE_STATE_FILE="${CONFIG_DIR}/last-notified-failure.sha256"

mkdir -p "${CONFIG_DIR}"
chmod 700 "${CONFIG_DIR}"

notify_user() {
    local title="$1"
    local message="$2"

    /usr/bin/osascript - "${title}" "${message}" <<'APPLESCRIPT' >/dev/null 2>&1 || true
on run argv
    display notification (item 2 of argv) with title (item 1 of argv)
end run
APPLESCRIPT
}

latest_sync_error() {
    if [[ ! -r "${LOG_FILE}" ]]; then
        return 0
    fi
    /usr/bin/tail -n 160 "${LOG_FILE}" \
        | /usr/bin/awk '/^\{"ok": false, "error": / { line = $0 } END { print line }'
}

started_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
"${SYNC_SCRIPT}"
exit_code=$?
finished_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
error_line=""

if (( exit_code != 0 )); then
    error_line="$(latest_sync_error)"
    [[ -n "${error_line}" ]] || error_line="同步脚本退出码 ${exit_code}，请查看 ${LOG_FILE}"
fi

status_tmp="${STATUS_FILE}.tmp.$$"
{
    printf 'started_at=%s\n' "${started_at}"
    printf 'finished_at=%s\n' "${finished_at}"
    printf 'exit_code=%s\n' "${exit_code}"
    printf 'log_file=%s\n' "${LOG_FILE}"
    printf 'error=%s\n' "${error_line}"
} >"${status_tmp}"
chmod 600 "${status_tmp}"
mv "${status_tmp}" "${STATUS_FILE}"

if (( exit_code != 0 )); then
    failure_fingerprint="$(printf '%s\n%s\n' "${exit_code}" "${error_line}" | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}')"
    previous_fingerprint=""
    [[ -r "${FAILURE_STATE_FILE}" ]] && previous_fingerprint="$(<"${FAILURE_STATE_FILE}")"

    if [[ "${failure_fingerprint}" != "${previous_fingerprint}" ]]; then
        printf '%s\n' "${failure_fingerprint}" >"${FAILURE_STATE_FILE}"
        chmod 600 "${FAILURE_STATE_FILE}"
        notification_message="$(printf '%s' "${error_line}" | /usr/bin/cut -c 1-180)"
        notify_user "RSC OAM 同步失败" "${notification_message}"
    fi
else
    if [[ -e "${FAILURE_STATE_FILE}" ]]; then
        rm -f "${FAILURE_STATE_FILE}"
        notify_user "RSC OAM 同步已恢复" "最新增量同步已完成并通过校验。"
    fi
fi

exit "${exit_code}"
