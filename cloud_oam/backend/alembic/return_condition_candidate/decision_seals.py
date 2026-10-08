"""Private decision closure candidate; requires the complete initial seal installation."""
from pathlib import Path
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import AddConstraint, CreateTable
from app.return_condition_seal_schema import NAME as INITIAL_SEAL
from app.return_condition_decision_seal_schema import NAME, SOURCE_CONSTRAINT, define
from app.return_condition_schema import CASES
from app.return_condition_key_schema import ALIASES, KEY_DOMAINS
from app.formal_services.stock_loss_corrections.return_condition_coordinates import LEGACY_TABLES, HASH_COLUMNS


def statements(metadata, *, additional_request_tables=(), sql_folder=None):
    # Explicit additional coordinate-bearing tables must already exist. Never
    # silently omit a new retained request when composing the shared schema.
    additional_request_tables = tuple(additional_request_tables)
    if (len(set(additional_request_tables)) != len(additional_request_tables)
            or set(additional_request_tables) & {*LEGACY_TABLES, INITIAL_SEAL, NAME}):
        raise ValueError('duplicate or previously covered condition request table')
    for name in additional_request_tables:
        target = metadata.tables.get(name)
        if (target is None or len(target.primary_key.columns) != 1
                or not {'actor_user_id', 'request_id', 'idempotency_key_hash'} <= set(target.c.keys())):
            raise ValueError('complete retained request coordinates required: ' + name)
    required = {'stock_condition_settlement_requests', 'stock_condition_settlement_scans'}
    if (not required <= set(metadata.tables)
            or 'stock_condition_settlement_requests' not in additional_request_tables):
        raise ValueError('complete settlement input and reciprocal request fence required')
    table = define(metadata)
    participating = (*LEGACY_TABLES, INITIAL_SEAL, NAME, *additional_request_tables)
    roles = 'PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox'
    source_constraint = next(c for c in metadata.tables[CASES].constraints if c.name == SOURCE_CONSTRAINT)
    result = [str(AddConstraint(source_constraint).compile(dialect=dialect())),
        str(CreateTable(table).compile(dialect=dialect())),
        f'REVOKE ALL ON TABLE public.{NAME} FROM {roles}',
        f'GRANT SELECT ON TABLE public.{NAME} TO star_oam_api,star_oam_backup']
    folder = Path(sql_folder) if sql_folder is not None else Path(__file__).parent
    domains = ','.join("('%s','%s')" % pair for pair in KEY_DOMAINS)
    result.extend((folder / name).read_text().replace('__KEY_DOMAINS__', domains)
        for name in ('decision_seal_input.sql', 'decision_seal_permission.sql', 'decision_seal_source.sql'))
    sql = (folder / 'decision_seal_persistence.sql').read_text()
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
            "RAISE EXCEPTION 'condition decision sealed key collides with business evidence' USING ERRCODE='23514'; END IF;")
    delivery = []
    for name, kind, identifier in (('outbox_events', 'aggregate_type', 'aggregate_id'),
                                    ('notification_events', 'business_type', 'business_id')):
        delivery.append(f"""IF EXISTS(SELECT 1 FROM public.{name} n
            WHERE (n.payload_jsonb->>'request_id'=s.request_id OR n.payload_jsonb->>'request_reference'=reference)
            AND (n.payload_jsonb->>'actor_user_id'=s.actor_user_id OR n.payload_jsonb->>'actor_person_id'=s.actor_person_id::text
                OR EXISTS(SELECT 1 FROM public.audit_events a WHERE a.aggregate_type=n.{kind} AND a.aggregate_id=n.{identifier} AND a.actor_user_id=s.actor_user_id)
                OR NOT EXISTS(SELECT 1 FROM public.audit_events a WHERE a.aggregate_type=n.{kind} AND a.aggregate_id=n.{identifier} AND a.actor_user_id<>s.actor_user_id))) THEN
            RAISE EXCEPTION 'condition decision sealed request has delivery evidence' USING ERRCODE='23514'; END IF;""")
    sql = sql.replace('__ALIASES__', quoted(ALIASES)).replace('__HASH_COLUMNS__', quoted(HASH_COLUMNS))
    sql = sql.replace('__KEY_DOMAINS__', ','.join("('"+name+"','"+prefix+"')" for name,prefix in KEY_DOMAINS))
    sql = sql.replace('__COLLISION_CHECKS__', '\n'.join(checks)).replace('__DELIVERY_CHECKS__', '\n'.join(delivery))
    sql = sql.replace('__REVERSE_ALIASES__', ' OR '.join('s.'+name+'=ANY(keys)' for name in ALIASES))
    result.append(sql)
    for operation, suffix, level in (('INSERT OR UPDATE OR DELETE', 'input_and_immutable', 'ROW'), ('TRUNCATE', 'no_truncate', 'STATEMENT')):
        result.extend([f'CREATE TRIGGER condition_decision_seal_{suffix} BEFORE {operation} ON public.{NAME} '
            f'FOR EACH {level} EXECUTE FUNCTION public.rsc_condition_decision_seal_input_guard()',
            f'ALTER TABLE public.{NAME} ENABLE ALWAYS TRIGGER condition_decision_seal_{suffix}'])
    for name in (*participating, 'audit_events', 'state_transition_events', 'outbox_events', 'notification_events'):
        result.extend([f'CREATE TRIGGER condition_decision_seal_lock BEFORE INSERT OR UPDATE OR DELETE ON public.{name} '
            'FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_decision_seal_lock()',
            f'ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER condition_decision_seal_lock',
            f'CREATE CONSTRAINT TRIGGER condition_decision_seal_fence AFTER INSERT OR UPDATE OR DELETE ON public.{name} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_decision_seal_fence()',
            f'ALTER TABLE public.{name} ENABLE ALWAYS TRIGGER condition_decision_seal_fence'])
    return table, result
