# Sourced by POSIX backup entry points. All probes and limits stay in children.
capacity_error() { printf '%s\n' "$1" >&2; return 64; }
capacity_validate() {
  case "${RSC_BACKUP_MAXIMUM_BYTES:-}" in ''|0*|*[!0-9]*) capacity_error RSC_BACKUP_CAPACITY_INVALID; return 64;; esac
  [ "${#RSC_BACKUP_MAXIMUM_BYTES}" -le 13 ] || return 64
  [ "$RSC_BACKUP_MAXIMUM_BYTES" -ge 1048576 ] &&
    [ "$RSC_BACKUP_MAXIMUM_BYTES" -le 1099511627776 ] &&
    [ "$((RSC_BACKUP_MAXIMUM_BYTES % 1024))" -eq 0 ] || {
      capacity_error RSC_BACKUP_CAPACITY_INVALID; return 64;
    }
}
capacity_free() {
  # POSIX -P and -k give available space in 1024-byte units, including paths
  # containing spaces. This is a point-in-time check, not a disk reservation.
  capacity_available=$(LC_ALL=C df -Pk "$1" | awk 'NR==2 {print $4}')
  case "$capacity_available" in ''|*[!0-9]*) capacity_error RSC_BACKUP_DISK_UNKNOWN; return 64;; esac
  [ "$capacity_available" -ge "$((RSC_BACKUP_MAXIMUM_BYTES / 1024 * $2))" ] || {
    capacity_error RSC_BACKUP_DISK_INSUFFICIENT; return 64;
  }
}
capacity_prepare() {
  capacity_validate || return 64
  capacity_free "$1" "$2" || return 64
  # Shells differ on -f units. Measure a child limit with a <=2 KiB probe;
  # refuse unknown units instead of silently doubling the byte budget.
  capacity_unit=$(
    capacity_probe=$(mktemp "$1/.rsc-capacity.XXXXXX") || exit 64
    trap 'rm -f -- "$capacity_probe"' EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    ( (ulimit -c 0; ulimit -f 1; dd if=/dev/zero bs=1 count=2048 > "$capacity_probe") 2>/dev/null ) 2>/dev/null || :
    wc -c < "$capacity_probe" | tr -d ' '
  ) || return 64
  case "$capacity_unit" in 512|1024) ;; *) capacity_error RSC_BACKUP_FILE_LIMIT_UNSUPPORTED; return 64;; esac
  RSC_BACKUP_LIMIT_BLOCKS=$((RSC_BACKUP_MAXIMUM_BYTES / capacity_unit))
}
capacity_run() (
  # Regular outputs opened by caller redirections inherit this exact byte cap.
  # No pipeline can hide the exit status of the actual exporter.
  ulimit -c 0
  ulimit -f "$RSC_BACKUP_LIMIT_BLOCKS" || exit 64
  exec "$@"
)
