#!/usr/bin/env bash

set -euo pipefail

failures=0
candidate_count=0
candidate_bytes=0
max_file_bytes=$((10 * 1024 * 1024))
baseline_path='docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md'

report_failure() {
  printf 'repository-safety: %s\n' "$1" >&2
  failures=$((failures + 1))
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
  printf 'repository-safety: no Git repository found\n' >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
expected_repo_root="$(cd "${script_dir}/../.." && pwd -P)"
repo_root="$(cd "$repo_root" && pwd -P)"
if [[ "$repo_root" != "$expected_repo_root" ]]; then
  printf 'repository-safety: Git root does not match the reviewed oam repository boundary\n' >&2
  exit 1
fi
cd "$repo_root"

for required_path in README.md AGENTS.md "$baseline_path" cloud_oam/README.md cloud_oam/.gitignore; do
  if [[ ! -f "$required_path" ]]; then
    report_failure "required repository file is missing: $required_path"
  fi
done

# These workspace paths may contain production sessions, operational evidence,
# or generated business data.  The root allowlist must keep them invisible to
# both ordinary `git add .` and future account handoffs.
for protected_path in work output outputs scripts tmp; do
  if [[ -e "$protected_path" ]] && ! git check-ignore -q -- "$protected_path"; then
    report_failure "protected workspace path is not ignored: $protected_path"
  fi
done

high_confidence_secret_pattern='-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|LTAI[A-Za-z0-9]{12,}|AIza[0-9A-Za-z_-]{30,}|xox[baprs]-[0-9A-Za-z-]{20,}|sk-[A-Za-z0-9]{24,}|ya29\.[0-9A-Za-z_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|(postgres(ql)?|mysql|mongodb(\+srv)?)://[^[:space:]/:@]+:[^[:space:]@]+@'

while IFS= read -r -d '' staged_entry; do
  staged_mode="${staged_entry%% *}"
  if [[ "$staged_mode" == "160000" ]]; then
    staged_path="${staged_entry#*$'\t'}"
    report_failure "Git submodule/gitlink is not permitted: $staged_path"
  fi
done < <(git ls-files --stage -z)

while IFS= read -r -d '' path; do
  candidate_count=$((candidate_count + 1))

  case "$path" in
    .gitignore|README.md|AGENTS.md|"$baseline_path"|cloud_oam/*)
      ;;
    *)
      report_failure "path escapes the explicit repository allowlist: $path"
      ;;
  esac

  case "$path" in
    cloud_oam/.env.example)
      ;;
    */.env|*/.env.*|*.env|*/runtime.env|*/*.runtime.env|*/.qa-cookie|*/project.private.config.json|\
    */.npmrc|*/.netrc|*/.pypirc|*/id_rsa*|*/id_ed25519*|*/.aws/*|*/.ssh/*|*/.secrets/*|\
    */node_modules/*|*/.pnpm-store/*|*/.venv/*|*/dist/*|*/__pycache__/*|*/.pytest_cache/*|\
    */.demo_uploads/*|*/.qa_uploads/*|*/.test_uploads/*|*/.uploads/*|*/uploads/*|\
    */artifacts/*|*/exports/*|*/backups/*|*/outbox/*|*/inbox/*|*/quarantine/*|*/runtime/*|*/tmp/*|\
    *.db|*.db-*|*.sqlite|*.sqlite-*|*.sqlite3|*.sqlite3-*|*.log|*.pid|\
    *.pem|*.key|*.p12|*.pfx|*.crt|*.cer|*.jks|*.kdbx|*.mobileprovision|\
    *.zip|*.tar|*.tar.gz|*.tgz|*.7z|*.csv|*.tsv|*.xlsx|*.xls|*.jsonl|*.parquet|\
    *.dump|*.backup|*.bak|*.har)
      report_failure "forbidden runtime, credential, or business-data artifact is Git-visible: $path"
      ;;
  esac

  case "${path##*/}" in
    *[Ss][Ee][Ss][Ss][Ii][Oo][Nn]*.json|*[Cc][Oo][Oo][Kk][Ii][Ee]*.json|\
    *[Cc][Rr][Ee][Dd][Ee][Nn][Tt][Ii][Aa][Ll]*.json|*[Ss][Ee][Cc][Rr][Ee][Tt]*.json|\
    *[Tt][Oo][Kk][Ee][Nn]*.json)
      report_failure "session, cookie, or credential artifact is Git-visible: $path"
      ;;
  esac

  if [[ -L "$path" ]]; then
    report_failure "symbolic links are not permitted in the cloud repository: $path"
    continue
  fi

  if [[ -f "$path" ]]; then
    file_bytes="$(wc -c < "$path" | tr -d '[:space:]')"
    candidate_bytes=$((candidate_bytes + file_bytes))
    if (( file_bytes > max_file_bytes )); then
      report_failure "file exceeds the 10 MiB repository limit: $path"
    fi

    if LC_ALL=C grep -Iq . "$path" && \
      LC_ALL=C grep -Eq -- "$high_confidence_secret_pattern" "$path"; then
      report_failure "high-confidence credential pattern detected (content withheld): $path"
    fi

    if LC_ALL=C grep -Iq . "$path" && \
      LC_ALL=C grep -Eo '/Users/[A-Za-z0-9._-]+/' "$path" \
        | LC_ALL=C grep -Evq '^/Users/replace-with-local-user/$'; then
      report_failure "personal macOS home path detected (content withheld): $path"
    fi
  fi
done < <(git ls-files -co --exclude-standard -z)

if (( candidate_count == 0 )); then
  report_failure 'repository has no tracked or unignored candidate files'
fi

if (( failures > 0 )); then
  printf 'repository-safety: FAILED with %d issue(s)\n' "$failures" >&2
  exit 1
fi

printf 'repository-safety: PASS (%d candidate files, %d bytes; protected local paths ignored)\n' \
  "$candidate_count" "$candidate_bytes"
