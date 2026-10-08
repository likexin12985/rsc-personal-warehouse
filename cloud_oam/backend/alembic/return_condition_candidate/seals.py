"""Complete private seal candidate, installed after input/key/effect guards."""
from pathlib import Path
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable
from app.return_condition_seal_schema import NAME, define
from app.return_condition_key_schema import ALIASES, KEY_DOMAINS
from app.formal_services.stock_loss_corrections.return_condition_coordinates import LEGACY_TABLES, HASH_COLUMNS


def statements(metadata):
    table = define(metadata)
    participating = (*LEGACY_TABLES, NAME)
    roles = 'PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox'
    result = [str(CreateTable(table).compile(dialect=dialect())),
        f'REVOKE ALL ON TABLE public.{NAME} FROM {roles}',
        f'GRANT SELECT ON TABLE public.{NAME} TO star_oam_api,star_oam_backup']
    folder = Path(__file__).parent
    result.extend((folder / name).read_text() for name in ('seal_request.sql', 'seal_authority.sql'))
    sql = (folder / 'seal_persistence.sql').read_text()
    quoted = lambda values: ','.join("'"+v.replace("'", "''")+"'" for v in values)
    checks = []
    for name in participating:
        target = metadata.tables[name]
        terms = []
        if 'actor_user_id' in target.c and 'request_id' in target.c:
            terms.append('(t.actor_user_id=s.actor_user_id AND t.request_id=s.request_id)')
        terms.extend('t.'+col+'=ANY(keys)' for col in HASH_COLUMNS if col in target.c)
        if 'key_token' in target.c:
            terms.append('t.key_token=s.key_token')
        assert terms and len(target.primary_key.columns) == 1
        exclude = ' AND t.id<>s.id' if name == NAME else ''
        checks.append('IF EXISTS(SELECT 1 FROM public.'+name+' t WHERE ('+' OR '.join(terms)+')'+exclude+') THEN '
            "RAISE EXCEPTION 'condition sealed key collides with business evidence' USING ERRCODE='23514'; END IF;")
    delivery = []
    for name, kind, identifier in (('outbox_events', 'aggregate_type', 'aggregate_id'),
                                    ('notification_events', 'business_type', 'business_id')):
        delivery.append(f"""IF EXISTS(SELECT 1 FROM public.{name} n
            WHERE (n.payload_jsonb->>'request_id'=s.request_id OR n.payload_jsonb->>'request_reference'=reference)
            AND (n.payload_jsonb->>'actor_user_id'=s.actor_user_id OR n.payload_jsonb->>'actor_person_id'=s.actor_person_id::text
                OR EXISTS(SELECT 1 FROM public.audit_events a WHERE a.aggregate_type=n.{kind} AND a.aggregate_id=n.{identifier} AND a.actor_user_id=s.actor_user_id)
                OR NOT EXISTS(SELECT 1 FROM public.audit_events a WHERE a.aggregate_type=n.{kind} AND a.aggregate_id=n.{identifier} AND a.actor_user_id<>s.actor_user_id))) THEN
            RAISE EXCEPTION 'condition sealed request has delivery evidence' USING ERRCODE='23514'; END IF;""")
    sql = sql.replace('__ALIASES__', quoted(ALIASES)).replace('__HASH_COLUMNS__', quoted(HASH_COLUMNS))
    sql = sql.replace('__KEY_DOMAINS__', ','.join("('"+name+"','"+prefix+"')" for name,prefix in KEY_DOMAINS))
    sql = sql.replace('__COLLISION_CHECKS__', '\n'.join(checks)).replace('__DELIVERY_CHECKS__', '\n'.join(delivery))
    sql = sql.replace('__REVERSE_ALIASES__', ' OR '.join('s.'+name+'=ANY(keys)' for name in ALIASES))
    result.append(sql)
    for operation, suffix, level in (('UPDATE OR DELETE', 'immutable', 'ROW'), ('TRUNCATE', 'no_truncate', 'STATEMENT')):
        result.extend([f'CREATE TRIGGER condition_seal_{suffix} BEFORE {operation} ON public.{NAME} '
            f'FOR EACH {level} EXECUTE FUNCTION public.rsc_condition_reject_mutation()',
            f'ALTER TABLE public.{NAME} ENABLE ALWAYS TRIGGER condition_seal_{suffix}'])
    for name in (*participating, 'audit_events', 'state_transition_events', 'outbox_events', 'notification_events'):
        result.extend([f'CREATE TRIGGER condition_seal_lock BEFORE INSERT OR UPDATE OR DELETE ON public.{name} '
            'FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_seal_lock()',
            f'ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER condition_seal_lock',
            f'CREATE CONSTRAINT TRIGGER condition_seal_fence AFTER INSERT OR UPDATE OR DELETE ON public.{name} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_seal_fence()',
            f'ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER condition_seal_fence'])
    return table, result
