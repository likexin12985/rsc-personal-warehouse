"""Maintain verified capture SELECT grants inside the migration transaction.

Frozen revisions still see and verify their original catalogs. No application
settings, models, role credentials or business data are loaded here. The pure
capture-role contract remains the same one checked by readers and provisioning.
Any failure propagates to Alembic's outer transaction, including failed grant
restoration; there is no separately committed permission change.
"""
from contextlib import contextmanager

from sqlalchemy import text


def capture_acl(connection, roles):
    """Include grantor identity; restoration must preserve actual grants."""
    return tuple(connection.execute(text("""
        SELECT n.nspname,c.relname,pg_get_userbyid(a.grantor),
               pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          CROSS JOIN LATERAL aclexplode(c.relacl) a
         WHERE a.grantee IN (SELECT oid FROM pg_roles WHERE rolname=ANY(:roles))
         ORDER BY 1,2,3,4,5,6
    """), {'roles': sorted(roles)}).all())


@contextmanager
def maintain_capture_permissions(connection):
    if connection.dialect.name != 'postgresql':
        yield
        return
    if not connection.in_transaction():
        raise RuntimeError('capture migration requires the caller transaction')
    from app.daily_reconciliation.capture_role_contract import ROLES, ALL_TABLES
    from app.daily_reconciliation.capture_security import validate_capture_roles

    present = connection.execute(text('SELECT rolname FROM pg_roles WHERE rolname=ANY(:roles)'),
                                 {'roles': sorted(ROLES)}).scalars().all()
    if not present:
        yield
        return
    identity = connection.execute(text("SELECT current_user,session_user,"
        "current_setting('server_version_num')::int,current_setting('transaction_isolation'),"
        "current_setting('transaction_read_only'),rolsuper,rolcreatedb,rolcreaterole,"
        "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
    if (identity[:2] != ('star_oam_migrator','star_oam_migrator') or identity[2]//10000 != 16
            or identity[3:5] != ('read committed','off') or any(identity[5:])):
        raise RuntimeError('capture migration requires direct unprivileged PG16 migrator')
    # Same lock as supported capture provisioning. env.py has already locked
    # the version table, preventing a competing provisioner's head read.
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended("
        "'rsc.daily.capture.role-provisioning',0))"))
    validate_capture_roles(connection)
    quote = connection.dialect.identifier_preparer.quote
    tables = ','.join('public.'+quote(name) for name in sorted(ALL_TABLES))
    connection.execute(text('LOCK TABLE '+tables+' IN ACCESS EXCLUSIVE MODE'))
    validate_capture_roles(connection)
    before = capture_acl(connection, ROLES)
    expected = tuple(sorted(('public',name,'star_oam_migrator',role,'SELECT',False)
        for role,(names,_) in ROLES.items() for name in names))
    if before != expected:
        raise RuntimeError('capture migration requires exact owner-issued SELECT grants')
    for role,(names,_) in ROLES.items():
        names = ','.join('public.'+quote(name) for name in names)
        connection.execute(text('REVOKE SELECT ON '+names+' FROM '+quote(role)))
    if capture_acl(connection, ROLES):
        raise RuntimeError('capture migration permission maintenance incomplete')

    yield

    # A target revision that removes required capture tables cannot preserve
    # this installed role contract. GRANT/validation fail and the entire
    # migration rolls back; roles and their privileges are never discarded.
    for role,(names,_) in ROLES.items():
        names = ','.join('public.'+quote(name) for name in names)
        connection.execute(text('GRANT SELECT ON '+names+' TO '+quote(role)))
    if capture_acl(connection, ROLES) != before:
        raise RuntimeError('capture migration grants were not restored exactly')
    validate_capture_roles(connection)
