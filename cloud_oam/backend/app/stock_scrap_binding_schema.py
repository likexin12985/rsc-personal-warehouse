"""Complete relational model for database-owned scrap request provenance."""
from sqlalchemy import Column, DateTime, ForeignKey, String, Table, CheckConstraint, UniqueConstraint
from app.foundation_models import UUID_TYPE

NAME = 'stock_scrap_request_key_bindings'
ALIASES = ('reversal_key_hash', 'approval_key_hash', 'correction_key_hash', 'recovery_key_hash', 'scrap_key_hash')
TYPED = dict(original='original_id', apply='application_id', regional='regional_id', headquarters='headquarters_id')


def digest_check(table, name, expression, *, nullable=False):
    prefix = expression + ' IS NULL OR ' if nullable else ''
    table.append_constraint(CheckConstraint(prefix + expression + " ~ '^[a-f0-9]{64}$'",
        name=name).ddl_if(dialect='postgresql'))
    table.append_constraint(CheckConstraint(prefix + '(length(' + expression + ')=64 AND ' +
        expression + " NOT GLOB '*[^a-f0-9]*')", name=name).ddl_if(dialect='sqlite'))


def define(metadata):
    if NAME in metadata.tables:
        raise ValueError('scrap request registry already defined')
    targets = dict(root_disposition_id='stock_loss_dispositions', actor_person_id='people',
        original_id='stock_loss_dispositions', application_id='stock_scrap_recovery_requests',
        regional_id='stock_scrap_recovery_regional_reviews', headquarters_id='stock_scrap_recovery_headquarters_reviews')
    if not all(target in metadata.tables for target in targets.values()) or 'users' not in metadata.tables:
        raise ValueError('complete scrap registry parent metadata required')
    def reference(name, *, nullable=False):
        return Column(name, UUID_TYPE, ForeignKey(targets[name] + '.id', ondelete='RESTRICT',
            name=NAME + '_' + name + '_fkey'), nullable=nullable)
    table = Table(NAME, metadata,
        Column('fact_id', UUID_TYPE, primary_key=True), Column('binding_kind', String(24), nullable=False),
        reference('root_disposition_id'),
        Column('actor_user_id', String(36), ForeignKey('users.id', ondelete='RESTRICT',
            name=NAME + '_actor_user_id_fkey'), nullable=False),
        reference('actor_person_id'), Column('request_id', String(160), nullable=False),
        Column('request_hash', String(64), nullable=False), Column('key_token', String(64), nullable=False),
        *(Column(name, String(64), nullable=False) for name in ALIASES),
        *(reference(name, nullable=True) for name in TYPED.values()),
        Column('created_at', DateTime(timezone=True), nullable=False),
        UniqueConstraint('actor_user_id', 'request_id', name=NAME + '_actor_user_id_request_id_key'),
        *(UniqueConstraint(name, name=NAME + '_' + name + '_key') for name in ('key_token', *ALIASES)),
        CheckConstraint(' OR '.join('(' + ' AND '.join([
            "binding_kind='" + kind + "'", selected + ' IS NOT NULL', selected + '=fact_id',
            *([selected + '=root_disposition_id'] if kind == 'original' else []),
            *(other + ' IS NULL' for other in TYPED.values() if other != selected)]) + ')'
            for kind, selected in TYPED.items()), name=NAME + '_check'),
        CheckConstraint(' AND '.join(name + ' NOT IN (' + ','.join(ALIASES[i+1:]) + ')'
            for i, name in enumerate(ALIASES[:-2])) + ' AND recovery_key_hash<>scrap_key_hash',
            name=NAME + '_check1'))
    for name in ('request_hash', 'key_token', *ALIASES):
        digest_check(table, NAME + '_' + name + '_check', name)
    table.append_constraint(CheckConstraint("request_id ~ '^[A-Za-z0-9._:-]{8,160}$'",
        name=NAME + '_request_id_check').ddl_if(dialect='postgresql'))
    table.append_constraint(CheckConstraint("length(request_id) BETWEEN 8 AND 160 AND request_id NOT GLOB '*[^A-Za-z0-9._:-]*'",
        name=NAME + '_request_id_check').ddl_if(dialect='sqlite'))
    return table


def extend_legacy(metadata):
    table = metadata.tables['stock_loss_request_key_bindings']
    if any(name in table.c for name in ('recovery_key_hash', 'scrap_key_hash')):
        raise ValueError('legacy scrap aliases already defined')
    for name, kind in (('recovery_key_hash', 'inverse'), ('scrap_key_hash', 'correction')):
        table.append_column(Column(name, String(64)))
        suffix = 'recovery' if kind == 'inverse' else 'scrap'
        table.append_constraint(UniqueConstraint(name, name='uq_loss_binding_' + suffix + '_0165'))
        prefix = name + " IS NULL OR (binding_kind='" + kind + "' AND "
        if kind == 'correction':
            prefix += 'recovery_key_hash IS NULL AND '
        ending = ' AND ' + name + ' NOT IN (reversal_key_hash,approval_key_hash,correction_key_hash))'
        table.append_constraint(CheckConstraint(prefix + name + " ~ '^[a-f0-9]{64}$'" + ending,
            name='ck_loss_binding_' + suffix + '_0165').ddl_if(dialect='postgresql'))
        table.append_constraint(CheckConstraint(prefix + '(length(' + name + ')=64 AND ' + name +
            " NOT GLOB '*[^a-f0-9]*')" + ending,
            name='ck_loss_binding_' + suffix + '_0165').ddl_if(dialect='sqlite'))
    return table
