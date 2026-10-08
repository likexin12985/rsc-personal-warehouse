"""Frozen additive request closure role defaults for a reviewed migration.

No live models, account creation, assignment changes, wildcard grant or commit.
Explicit deny and custom permission/grant identities are preserved. Reapplying
the same policy is a no-op. Application rollback retains authorization data;
it must never silently revoke grants or remove historical configuration.
"""
from datetime import datetime, timezone
from uuid import UUID, uuid5

import sqlalchemy as sa

NAMESPACE = UUID('988a6ffb-2497-51e0-856d-d908961da71d')
ROLES = {
    'admin': UUID('10000000-0000-4000-8000-000000000001'),
    'provincial_manager': UUID('10000000-0000-4000-8000-000000000002'),
}
DEFAULTS = (('admin', 'close'), ('provincial_manager', 'close'))


def permission_id(action):
    return uuid5(NAMESPACE, 'permission:material_request:' + action)


def grant_id(role, action):
    return uuid5(NAMESPACE, 'grant:' + role + ':material_request:' + action)


def tables(db):
    schema = 'public' if db.dialect.name == 'postgresql' else None
    def table(name, *columns):
        return sa.table(name, *[sa.column(key, kind) for key, kind in columns], schema=schema)
    return dict(
        roles=table('roles', ('id', sa.Uuid()), ('code', sa.String()),
            ('is_external', sa.Boolean()), ('status', sa.String())),
        permissions=table('permissions', ('id', sa.Uuid()), ('resource', sa.String()),
            ('action', sa.String()), ('field_code', sa.String()), ('description', sa.String()),
            ('created_at', sa.DateTime(timezone=True)), ('updated_at', sa.DateTime(timezone=True))),
        grants=table('role_permissions', ('id', sa.Uuid()), ('role_id', sa.Uuid()),
            ('permission_id', sa.Uuid()), ('effect', sa.String()), ('created_at', sa.DateTime(timezone=True))),
        users=table('users', ('id', sa.String()), ('authorization_version', sa.BigInteger())),
        assignments=table('role_assignments', ('user_id', sa.String()), ('role_id', sa.Uuid())),
    )


def _boundary(db):
    if not db.in_transaction():
        raise ValueError('permission policy requires a caller-owned transaction')
    if db.dialect.name == 'sqlite':
        return
    if db.dialect.name != 'postgresql':
        raise ValueError('permission policy requires PostgreSQL16 or SQLite tooling')
    row = db.execute(sa.text("""SELECT current_user,session_user,
        pg_catalog.current_setting('server_version_num')::int,pg_catalog.current_setting('transaction_isolation'),
        pg_catalog.current_setting('transaction_read_only'),rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
        FROM pg_catalog.pg_roles WHERE rolname=current_user""")).one()
    if (row[:2] != ('star_oam_migrator', 'star_oam_migrator') or row[2]//10000 != 16
            or row[3:5] != ('read committed', 'off') or any(row[5:])):
        raise ValueError('permission policy requires direct unprivileged PG16 migrator')
    # Freeze the graph in the same broad order as principal changes. No
    # external identity, session, or business record is inferred or rewritten.
    db.execute(sa.text('LOCK TABLE public.users,public.role_assignments,public.roles,'
        'public.permissions,public.role_permissions IN SHARE ROW EXCLUSIVE MODE'))


def _plan(db, t):
    roles = {row['code']: row for row in db.execute(sa.select(t['roles'])).mappings()}
    for code, identifier in ROLES.items():
        role = roles.get(code)
        if (role is None or role['id'] != identifier or role['is_external'] is not False
                or role['status'] not in ('active', 'inactive')):
            raise ValueError('permission policy role identity changed: ' + code)
    definitions = list(db.execute(sa.select(t['permissions'])).mappings())
    by_key = {(r['resource'], r['action'], r['field_code']): r for r in definitions}
    by_id = {r['id']: r for r in definitions}
    rows = list(db.execute(sa.select(t['grants'])).mappings())
    by_pair = {(r['role_id'], r['permission_id']): r for r in rows}
    grants_by_id = {r['id']: r for r in rows}
    if len(by_key) != len(definitions) or len(by_pair) != len(rows):
        raise ValueError('ambiguous permission definitions or role grants')
    now = datetime.now(timezone.utc)
    new_permissions, new_grants, retained_deny = [], [], []
    for role, action in DEFAULTS:
        natural = ('material_request', action, '')
        reserved = by_id.get(permission_id(action))
        if reserved is not None and (reserved['resource'], reserved['action'], reserved['field_code']) != natural:
            raise ValueError('reserved permission identity collision: ' + action)
        definition = by_key.get(natural)
        if definition is None:
            definition = dict(id=permission_id(action), resource=natural[0], action=action, field_code='',
                description='Formal V1 material request closure: ' + action, created_at=now, updated_at=now)
            new_permissions.append(definition)
            by_key[natural] = definition
            by_id[definition['id']] = definition
        pair = (ROLES[role], definition['id'])
        reserved_grant = grants_by_id.get(grant_id(role, action))
        if reserved_grant is not None and (reserved_grant['role_id'], reserved_grant['permission_id']) != pair:
            raise ValueError('reserved grant identity collision: ' + action)
        grant = by_pair.get(pair)
        if grant is None:
            new_grants.append(dict(id=grant_id(role, action), role_id=pair[0], permission_id=pair[1],
                                   effect='allow', created_at=now))
        elif grant['effect'] == 'deny':
            retained_deny.append(action)
        elif grant['effect'] != 'allow':
            raise ValueError('invalid existing grant effect: ' + action)
    changed_roles = {r['role_id'] for r in new_grants}
    users, assignments = t['users'], t['assignments']
    affected = list(db.execute(sa.select(users.c.id, users.c.authorization_version).where(users.c.id.in_(
        sa.select(assignments.c.user_id).where(assignments.c.role_id.in_(changed_roles))))
        .order_by(users.c.id)).all()) if changed_roles else []
    if any(type(row.authorization_version) is not int or not 1 <= row.authorization_version < 2**63-1
           for row in affected):
        raise ValueError('affected authorization version cannot be advanced')
    return new_permissions, new_grants, retained_deny, affected


def install(db):
    _boundary(db)
    t = tables(db)
    definitions, grants, deny, affected = _plan(db, t)
    # All collision, role and version checks precede the first mutation.
    if affected:
        db.execute(t['users'].update().where(t['users'].c.id.in_([r.id for r in affected]))
            .values(authorization_version=t['users'].c.authorization_version+1))
    if definitions:
        db.execute(t['permissions'].insert(), definitions)
    if grants:
        db.execute(t['grants'].insert(), grants)
    # Validate the resulting natural-key bindings without relying on rowcount.
    again = _plan(db, t)
    if again[0] or again[1] or again[3] or again[2] != deny:
        raise ValueError('permission policy writeback differs from its exact plan')
    versions = dict(db.execute(sa.select(t['users'].c.id, t['users'].c.authorization_version)
        .where(t['users'].c.id.in_([r.id for r in affected]))).all()) if affected else {}
    if any(versions.get(r.id) != r.authorization_version+1 for r in affected):
        raise ValueError('permission policy authorization invalidation mismatch')
    return dict(createdPermissions=len(definitions), createdGrants=len(grants),
                preservedExplicitDeny=deny, advancedUsers=len(affected))


def preserve_on_downgrade(db):
    """Role configuration is additive data, retained across application rollback.

    Do not delete seed rows: they may now be customized or referenced by old
    request evidence. Revocation is a separate audited authorization operation.
    The Alembic wrapper still restores its exact prior readiness/version.
    """
    _boundary(db)
    return dict(authorizationDataRetained=True, grantsRevoked=0, definitionsDeleted=0)
