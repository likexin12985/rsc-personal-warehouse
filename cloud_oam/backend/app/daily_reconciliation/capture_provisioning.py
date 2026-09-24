"""Bootstrap-only atomic capture-role setup; never called by an HTTP route."""
from sqlalchemy import text
from psycopg import sql
from .capture_role_contract import ALL_TABLES, ROLES
from .capture_security import validate_capture_roles, CaptureRoleSecurityError

REQUIRED_HEAD = '20261119_0140'

def require_bootstrap(connection, database):
    identity=connection.execute(text("SELECT current_database(),current_user,session_user,"
        "current_setting('server_version_num')::int,(SELECT rolsuper FROM pg_roles WHERE rolname=current_user)")).one()
    if identity[0]!=database or identity[1]!=identity[2] or not identity[4] or not 160000<=identity[3]<170000:
        raise CaptureRoleSecurityError('daily_capture_direct_bootstrap_required')

def set_timeouts(connection):
    connection.exec_driver_sql("SET LOCAL lock_timeout='5s'")
    connection.exec_driver_sql("SET LOCAL statement_timeout='20s'")
    connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='20s'")

def lock_provisioning(connection, *, tables=True):
    connection.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended('rsc.daily.capture.role-provisioning',0))")
    if tables:
        connection.exec_driver_sql('LOCK TABLE '+','.join('public.'+n for n in ALL_TABLES)+' IN ACCESS EXCLUSIVE MODE')

def require_head(connection):
    if tuple(connection.execute(text('SELECT version_num FROM public.alembic_version')).scalars())!=(REQUIRED_HEAD,):
        raise CaptureRoleSecurityError('daily_capture_schema_head_mismatch')

def checked_passwords(passwords):
    if not isinstance(passwords,dict) or set(passwords)!=set(ROLES):
        raise CaptureRoleSecurityError('daily_capture_distinct_passwords_required')
    for password in passwords.values():
        if not isinstance(password,str) or not 32<=len(password)<=512 or any(ord(c)<32 or ord(c)==127 for c in password) \
                or 'replace' in password.lower() or 'changeme' in password.lower():
            raise CaptureRoleSecurityError('daily_capture_password_invalid')
    if len(set(passwords.values()))!=len(ROLES):
        raise CaptureRoleSecurityError('daily_capture_distinct_passwords_required')
    return passwords

def password_verifier(connection, role, password):
    # libpq computes SCRAM locally; the clear password never appears in SQL.
    driver=connection.connection.driver_connection
    return driver.pgconn.encrypt_password(password.encode(),role.encode(),b'scram-sha-256').decode()

def provision_capture_roles(connection, *, database, passwords=None, apply=False):
    """Caller owns commit/rollback; partial or unsafe roles are never repaired."""
    require_bootstrap(connection,database)
    require_head(connection)
    set_timeouts(connection)
    if apply:
        lock_provisioning(connection)
    configured=validate_capture_roles(connection,allow_absent=True)
    result=dict(schema='rsc.daily_capture_role_configuration.v1',database=database,
                head=REQUIRED_HEAD,configured=configured,
                roles=[dict(role=role,tables=list(tables),policy=policy) for role,(tables,policy) in ROLES.items()],
                changed=False)
    if not apply or configured:
        return result
    checked_passwords(passwords)
    driver=connection.connection.driver_connection
    for role,(tables,policy) in ROLES.items():
        # libpq creates a SCRAM verifier locally. SQL never contains the supplied
        # clear-text password; neither verifier nor exception details are logged.
        verifier=password_verifier(connection,role,passwords[role])
        statement=sql.SQL('CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Identifier(role),sql.Literal(verifier))
        connection.exec_driver_sql(statement.as_string(driver))
        connection.exec_driver_sql(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database),sql.Identifier(role)).as_string(driver))
        connection.exec_driver_sql(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)).as_string(driver))
        connection.exec_driver_sql(sql.SQL('GRANT SELECT ON {} TO {}').format(sql.SQL(',').join(sql.Identifier('public',n) for n in tables),sql.Identifier(role)).as_string(driver))
        rows=connection.execute(text("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(:tables) AND c.relrowsecurity"),dict(tables=list(tables))).scalars().all()
        for name in rows:
            connection.exec_driver_sql(sql.SQL('CREATE POLICY {} ON public.{} FOR SELECT TO {} USING (true)').format(sql.Identifier(policy),sql.Identifier(name),sql.Identifier(role)).as_string(driver))
        connection.exec_driver_sql(sql.SQL('ALTER ROLE {} SET search_path=public').format(sql.Identifier(role)).as_string(driver))
        connection.exec_driver_sql(sql.SQL('ALTER ROLE {} SET default_transaction_read_only=on').format(sql.Identifier(role)).as_string(driver))
        connection.exec_driver_sql(sql.SQL('ALTER ROLE {} LOGIN').format(sql.Identifier(role)).as_string(driver))
    validate_capture_roles(connection)
    return dict(result,configured=True,changed=True)
