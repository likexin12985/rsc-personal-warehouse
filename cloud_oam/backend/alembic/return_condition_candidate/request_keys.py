"""Unpublished key registry and reverse fences, after all condition guards.

No existing function body is replaced and no historical fact is backfilled.
Install only with the full isolated candidate, not as a standalone migration.
"""
from pathlib import Path
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable
from app.return_condition_key_schema import NAME, ALIASES, KEY_DOMAINS, define
from app.formal_services.stock_loss_corrections.return_condition_coordinates import LEGACY_TABLES, HASH_COLUMNS


def statements(metadata):
    table = define(metadata)
    tables = tuple(dict.fromkeys((*LEGACY_TABLES, NAME)))
    result = [str(CreateTable(table).compile(dialect=dialect())),
        'REVOKE ALL ON TABLE public.'+NAME+' FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup',
        'GRANT SELECT ON TABLE public.'+NAME+' TO star_oam_api,star_oam_backup']
    quoted = lambda values: ','.join("'"+s.replace("'","''")+"'" for s in values)
    sql = Path(__file__).with_suffix('.sql').read_text()
    sql = sql.replace('__KEY_DOMAINS__', ','.join("('"+name+"','"+prefix+"')" for name,prefix in KEY_DOMAINS))
    sql = sql.replace('__ALIASES__',quoted(ALIASES)).replace('__TABLES__',quoted(tables))
    sql = sql.replace('__HASH_COLUMNS__',quoted(tuple(dict.fromkeys((*HASH_COLUMNS,*ALIASES)))))
    checks = []
    for name in tables:
        target = metadata.tables[name]
        terms = []
        if 'actor_user_id' in target.c and 'request_id' in target.c:
            terms.append('(t.actor_user_id=b.actor_user_id AND t.request_id=b.request_id)')
        terms.extend('t.'+col+'=ANY(keys)' for col in dict.fromkeys((*HASH_COLUMNS,*ALIASES)) if col in target.c)
        if 'key_token' in target.c:
            terms.append('t.key_token=b.key_token')
        pk = tuple(target.primary_key.columns)
        if len(pk)!=1 or not terms:
            raise ValueError('exact request-coordinate table required: '+name)
        permitted = ('e.id' if name in ('stock_condition_events',NAME) else
            "CASE WHEN e.kind='submit' THEN e.id END" if name=='stock_condition_submission_requests' else
            "CASE WHEN e.kind='submit' THEN e.case_id END" if name=='stock_operation_orders' else 'NULL::uuid')
        checks.append('IF EXISTS(SELECT 1 FROM public.'+name+' t WHERE ('+' OR '.join(terms)+') '
            'AND t.'+pk[0].name+' IS DISTINCT FROM ('+permitted+')) THEN '
            "RAISE EXCEPTION 'condition durable request collides with another action' USING ERRCODE='23514'; END IF;")
    sql = sql.replace('__COLLISION_CHECKS__','\n    '.join(checks))
    sql = sql.replace('__REVERSE_ALIASES__',' OR '.join('b.'+col+'=ANY(keys)' for col in ALIASES))
    result.append(sql)
    for name in tables:
        result.extend([
            f'CREATE TRIGGER condition_key_lock BEFORE INSERT OR UPDATE ON public.{name} '
                'FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_request_key_lock()',
            f'ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER condition_key_lock',
            f'CREATE CONSTRAINT TRIGGER condition_key_complete AFTER INSERT OR UPDATE ON public.{name} '
                'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_request_key_fence()',
            f'ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER condition_key_complete',
        ])
    return table,result
