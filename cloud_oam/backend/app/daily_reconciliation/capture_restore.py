"""Prepare disabled capture principals, then activate after a reviewed restore.

This does not load SQL or declare business data restored. The caller must first
verify the backup and restored facts. A cluster/database-bound role comment
ties every stage and retry to the same explicit restore request and backup.
"""
import json
import re
from uuid import UUID

from psycopg import sql
from sqlalchemy import text

from .capture_role_contract import ROLES
from .capture_security import (
    CaptureRoleSecurityError, assert_catalog, capture_role_rows,
    validate_capture_roles,
)
from .capture_provisioning import (
    REQUIRED_HEAD, checked_passwords, lock_provisioning, password_verifier,
    require_bootstrap, require_head, set_timeouts,
)

EMPTY_DATABASE_SQL = text("""
SELECT NOT EXISTS (
 SELECT 1 FROM pg_namespace WHERE nspname !~ '^pg_'
  AND nspname NOT IN ('public','information_schema')
) AND NOT EXISTS (
 SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
) AND NOT EXISTS (
 SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
) AND NOT EXISTS (
 SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
 WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
) AND NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname<>'plpgsql')
""")

ROLE_STATE_SQL = text("""
SELECT rolname, rolcanlogin,
 rolpassword IS NULL AS password_absent,
 COALESCE(rolpassword LIKE 'SCRAM-SHA-256$%',false) AS password_scram,
 shobj_description(oid,'pg_authid') AS marker
FROM pg_authid WHERE rolname=ANY(:roles) ORDER BY rolname
""")

def restore_coordinates(restore_id, backup_sha256):
    try:
        canonical=str(UUID(restore_id))
    except (ValueError, TypeError, AttributeError):
        raise CaptureRoleSecurityError('daily_capture_restore_id_invalid') from None
    if canonical!=restore_id or UUID(canonical).int==0:
        raise CaptureRoleSecurityError('daily_capture_restore_id_invalid')
    if not isinstance(backup_sha256,str) or not re.fullmatch('[0-9a-f]{64}',backup_sha256) \
            or backup_sha256=='0'*64:
        raise CaptureRoleSecurityError('daily_capture_restore_backup_digest_invalid')
    return canonical,backup_sha256

def _marker(connection, database, restore_id, backup_sha256, phase):
    identity=connection.execute(text("SELECT (SELECT system_identifier::text FROM pg_control_system()),"
        "(SELECT oid::text FROM pg_database WHERE datname=current_database())")).one()
    return json.dumps(dict(schema='rsc.daily_capture_restore.v1',head=REQUIRED_HEAD,
        database=database,cluster=identity[0],database_oid=identity[1],
        restore_id=restore_id,backup_sha256=backup_sha256,phase=phase),sort_keys=True)

def _comment(connection, role, marker):
    driver=connection.connection.driver_connection
    connection.exec_driver_sql(sql.SQL('COMMENT ON ROLE {} IS {}').format(
        sql.Identifier(role),sql.Literal(marker)).as_string(driver))

def _prepared_catalog(connection, *, empty):
    rows=capture_role_rows(connection,login_enabled=False)
    if empty:
        # Required tables deliberately do not exist before SQL restore. All
        # other privilege checks must pass, and the empty-schema test ensures
        # there are no relations on which an ACL could be concealed.
        if not connection.execute(EMPTY_DATABASE_SQL).scalar_one():
            raise CaptureRoleSecurityError('daily_capture_restore_empty_database_required')
        rows=[{k:v for k,v in row.items() if k!='table_safe'} for row in rows]
    assert_catalog(rows)

def manage_capture_restore(connection, *, database, restore_id, backup_sha256,
                           mode, passwords=None):
    """Caller commits only after success; no implicit repair or password replay."""
    if mode not in ('prepare-restore','check-restore','activate-restore'):
        raise CaptureRoleSecurityError('daily_capture_restore_mode_invalid')
    restore_coordinates(restore_id,backup_sha256)
    require_bootstrap(connection,database)
    set_timeouts(connection)
    if mode!='check-restore':
        lock_provisioning(connection,tables=False)
    prepared=_marker(connection,database,restore_id,backup_sha256,'prepared')
    active=_marker(connection,database,restore_id,backup_sha256,'active')
    states=connection.execute(ROLE_STATE_SQL,dict(roles=list(ROLES))).mappings().all()
    empty=connection.execute(EMPTY_DATABASE_SQL).scalar_one()
    result=dict(schema='rsc.daily_capture_restore_result.v1',database=database,
        restore_id=restore_id,backup_sha256=backup_sha256,head=REQUIRED_HEAD,
        roles=sorted(ROLES),changed=False,business_data_verified=False)
    if not states:
        if not empty or mode=='activate-restore':
            raise CaptureRoleSecurityError('daily_capture_restore_empty_database_required')
        if mode=='check-restore':
            return dict(result,phase='absent')
        driver=connection.connection.driver_connection
        for role in ROLES:
            connection.exec_driver_sql(sql.SQL('CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER '
                'NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL').format(
                sql.Identifier(role)).as_string(driver))
            connection.exec_driver_sql(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
                sql.Identifier(database),sql.Identifier(role)).as_string(driver))
            connection.exec_driver_sql(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(
                sql.Identifier(role)).as_string(driver))
            for setting in ('search_path=public','default_transaction_read_only=on'):
                connection.exec_driver_sql(sql.SQL('ALTER ROLE {} SET '+setting).format(
                    sql.Identifier(role)).as_string(driver))
            _comment(connection,role,prepared)
        _prepared_catalog(connection,empty=True)
        return dict(result,phase='prepared',changed=True)
    if len(states)!=len(ROLES) or {s['rolname'] for s in states}!=set(ROLES):
        raise CaptureRoleSecurityError('daily_capture_restore_partial_roles')
    if all(s['marker']==active and s['rolcanlogin'] and s['password_scram'] for s in states):
        require_head(connection)
        validate_capture_roles(connection)
        if mode=='prepare-restore':
            raise CaptureRoleSecurityError('daily_capture_restore_already_active')
        return dict(result,phase='active')
    if not all(s['marker']==prepared and not s['rolcanlogin'] and s['password_absent'] for s in states):
        raise CaptureRoleSecurityError('daily_capture_restore_marker_or_role_mismatch')
    if empty:
        _prepared_catalog(connection,empty=True)
        if mode=='activate-restore':
            raise CaptureRoleSecurityError('daily_capture_restore_not_loaded')
        return dict(result,phase='prepared')
    require_head(connection)
    if mode=='activate-restore':
        lock_provisioning(connection)
    _prepared_catalog(connection,empty=False)
    if mode=='prepare-restore':
        raise CaptureRoleSecurityError('daily_capture_restore_database_already_loaded')
    if mode=='check-restore':
        return dict(result,phase='restored_disabled')
    checked_passwords(passwords)
    driver=connection.connection.driver_connection
    for role in ROLES:
        verifier=password_verifier(connection,role,passwords[role])
        connection.exec_driver_sql(sql.SQL('ALTER ROLE {} PASSWORD {} LOGIN').format(
            sql.Identifier(role),sql.Literal(verifier)).as_string(driver))
        _comment(connection,role,active)
    validate_capture_roles(connection)
    return dict(result,phase='active',changed=True)
