"""Backup entry refuses redirection and preserves the outer failure boundary.

These are shell/transport contract checks. Actual RLS locks and SQL restoration
require PostgreSQL 16 acceptance; fake clients here are never database evidence.
"""
from pathlib import Path
import os
import shlex
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "deployment" / "backup"


def environment(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "OAM_"))}
    env.update(PATH=str(binary) + os.pathsep + env["PATH"], POSTGRES_DB="star_oam_test", RSC_BACKUP_MAXIMUM_BYTES="1048576", TMPDIR=str(tmp_path))
    return binary, env


@pytest.mark.parametrize("changes", [
    {"POSTGRES_DB": "host=unreachable.invalid dbname=unused"},
    {"POSTGRES_DB": "postgresql://unreachable.invalid/unused"},
    {"POSTGRES_DB": "unsafe database"},
    {"POSTGRES_DB": "x" * 64},
    {"PGHOST": "/tmp/no-socket,unreachable.invalid"},
    {"PGHOST": "127.0.0.1"},
    {"PGHOST": "relative-socket"},
    {"PGHOST": "/tmp/socket\nother"},
])
def test_invalid_connection_input_is_rejected_before_either_client(tmp_path, changes):
    binary, env = environment(tmp_path)
    marker = tmp_path / "client-called"
    for name in ("psql", "pg_dump"):
        path = binary / name
        path.write_text("#!/bin/sh\n: > " + shlex.quote(str(marker)) + "\nexit 66\n")
        path.chmod(0o700)
    result = subprocess.run(["sh", str(OPS / "backup_database.sh")], env={**env, **changes},
                            capture_output=True, timeout=5)
    assert result.returncode == 64 and not result.stdout
    assert not marker.exists()


@pytest.mark.parametrize("bad_client", [None, "psql", "pg_dump"])
def test_only_pg16_clients_run_with_fixed_role_and_sanitized_context(tmp_path, bad_client):
    binary, env = environment(tmp_path)
    capture = tmp_path / "client-context"
    env.update(PGHOST=str(tmp_path / "private-socket"), PGUSER="wrong_role", PGDATABASE="wrong_database",
               PGSERVICE="untrusted_service", PGSERVICEFILE="untrusted_service_file",
               PGOPTIONS="-c row_security=off", PGHOSTADDR="192.0.2.1")
    for name in ("psql", "pg_dump"):
        version = "17.1" if name == bad_client else "16.15"
        source = "#!/bin/sh\nif [ \"${1:-}\" = --version ]; then\n  printf '%s\\n' '" + name + " (PostgreSQL) " + version + "'\n  exit 0\nfi\n"
        source += "printf '%s\\n' \"$PGUSER\" \"$PGDATABASE\" \"${PGSERVICE-unset}\" \"${PGSERVICEFILE-unset}\" \"${PGOPTIONS-unset}\" \"${PGHOSTADDR-unset}\" \"$@\" > " + shlex.quote(str(capture)) + "\nexit 29\n"
        path = binary / name
        path.write_text(source)
        path.chmod(0o700)
    result = subprocess.run(["sh", str(OPS / "backup_database.sh")], env=env, capture_output=True, timeout=5)
    if bad_client:
        assert result.returncode == 64 and not capture.exists()
    else:
        assert result.returncode == 29
        lines = capture.read_text().splitlines()
        assert lines[:6] == ["star_oam_backup", "star_oam_test", "unset", "unset", "-c client_connection_check_interval=1000", "unset"]
        assert lines[6:] == ["-X", "-w", "-q", "--set=ON_ERROR_STOP=1", "--file", str(OPS / "lock.sql")]


def test_snapshot_is_created_after_locks_and_both_shell_failures_are_fatal():
    lock = (OPS / "lock.sql").read_text()
    snapshot = (OPS / "snapshot.sql").read_text()
    assert "READ COMMITTED READ ONLY" in lock and "ACCESS SHARE MODE NOWAIT" in lock
    assert lock.index("\\ir role.sql") < lock.index("\\gexec") < lock.index("snapshot.sh")
    assert lock.index("snapshot.sh") < lock.rindex("\\ir role.sql") < lock.index("ROLLBACK;")
    assert "REPEATABLE READ READ ONLY" in snapshot
    assert snapshot.index("inventory_unchanged") < snapshot.index("\\ir policies.sql") < snapshot.index("pg_export_snapshot()") < snapshot.index("dump.sh")
    for source, marker in [(lock, "RSC_BACKUP_SNAPSHOT_CHILD_FAILED"), (snapshot, "RSC_BACKUP_DUMP_CHILD_FAILED")]:
        assert "\\set ON_ERROR_STOP on" in source
        assert source.index("\\! sh") < source.index("\\if :SHELL_ERROR") < source.index("RAISE EXCEPTION '" + marker)
        assert source.index("RAISE EXCEPTION '" + marker) < source.index("ROLLBACK;")
    dump = (OPS / "dump.sh").read_text()
    assert '--snapshot="$RSC_BACKUP_SNAPSHOT"' in dump
    assert "--enable-row-security" in dump and "--lock-wait-timeout=1500ms" in dump
    assert "BYPASSRLS" not in lock + snapshot + dump
    policies = (OPS / "policies.sql").read_text()
    unrestricted_scope = policies.split("WITH applicable_policies AS", 1)[0]
    assert "NOT p.polpermissive" in unrestricted_scope
    assert "p.polcmd IN ('r','*')" in unrestricted_scope
    assert "polroles" not in unrestricted_scope
    assert "RSC_BACKUP_RESTRICTIVE_POLICY_REFUSED" in unrestricted_scope


@pytest.mark.parametrize("stage,exit_code", [("dump", 87), ("uploads", 88)])
def test_failed_child_never_publishes_or_removes_an_old_backup(tmp_path, stage, exit_code):
    from backup_test_support import simulate_entry
    result, backups, previous = simulate_entry(tmp_path, failure=stage)
    assert result.returncode == exit_code
    assert previous.read_bytes() == b'previous complete synthetic backup'
    assert list(backups.iterdir()) == [previous]
