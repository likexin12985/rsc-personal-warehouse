"""Readback for a 0051 historical fixture and atomic failed migration."""
from sqlalchemy import text
from pg16_legacy_opening_fixture import load_fixture


def snapshot(engine, *, tables=None, columns=None):
    """Exact old columns/rows, including empty tables, before migration."""
    result = {}
    with engine.connect() as db:
        names = tables if tables is not None else db.scalars(text("""SELECT tablename
            FROM pg_tables WHERE schemaname='public' ORDER BY tablename""")).all()
        for table in names:
            selected = columns[table]['columns'] if columns is not None else db.scalars(text("""SELECT column_name
                FROM information_schema.columns WHERE table_schema='public' AND table_name=:table
                ORDER BY ordinal_position"""), {'table': table}).all()
            quote = engine.dialect.identifier_preparer.quote
            sql = "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM (SELECT "
            sql += ','.join(quote(c) for c in selected) + ' FROM public.' + quote(table) + ') t'
            result[table] = dict(columns=list(selected), rows=db.scalar(text(sql)))
    return result


def legacy_catalog(engine):
    """Compare named SQL objects and privileges, excluding transient OIDs."""
    with engine.connect() as db:
        functions = db.execute(text("""SELECT p.proname,pg_get_function_identity_arguments(p.oid),
            p.prokind,p.prosecdef,p.provolatile,p.proisstrict,p.proparallel,p.proleakproof,
            pg_get_userbyid(p.proowner),p.proconfig,p.proacl::text,pg_get_function_result(p.oid),
            encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' ORDER BY 1,2""")).all()
        triggers = db.execute(text("""SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid)
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1,2""")).all()
        constraints = db.execute(text("""SELECT c.relname,t.conname,pg_get_constraintdef(t.oid),t.convalidated
            FROM pg_constraint t JOIN pg_class c ON c.oid=t.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' ORDER BY 1,2""")).all()
        indexes = db.execute(text("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2")).all()
        tables = db.execute(text("""SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,
            pg_get_userbyid(c.relowner),c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' ORDER BY 1""")).all()
        columns = db.execute(text("""SELECT table_name,column_name,ordinal_position,
            data_type,udt_schema,udt_name,domain_schema,domain_name,is_nullable,column_default,
            character_maximum_length,numeric_precision,numeric_scale,datetime_precision,
            collation_schema,collation_name,is_identity,identity_generation,is_generated,generation_expression
            FROM information_schema.columns WHERE table_schema='public'
            ORDER BY table_name,ordinal_position""")).all()
    return dict(functions=functions,triggers=triggers,constraints=constraints,indexes=indexes,tables=tables,columns=columns)


def assert_backfill(engine, evidence):
    with engine.connect() as db:
        row = db.execute(text("""SELECT request_jsonb,request_resolution_jsonb,request_sha256
            FROM stocktake_scope_count_completions WHERE id=:id"""), {'id': evidence['completion_id']}).one()
        assert row[0] == evidence['expected_request_jsonb']
        assert row[1] == evidence['expected_request_resolution_jsonb']
        assert row[2] == evidence['request_sha256'] == row[1]['request_sha256']
        item, = row[1]['items']
        assert item['target_type'] == 'observation' and item['target_id'] == str(evidence['observation_id'])
        assert item['request_ordinal'] == 1 and item['serial_alias_keys'] == [evidence['serial_alias_key']]
        assert db.scalar(text('SELECT count(*) FROM inventory_transactions')) == 0
        assert db.scalar(text('SELECT count(*) FROM inventory_movements')) == 0


def historical_facts(engine, *, columns=None):
    return snapshot(engine,tables=load_fixture()['tables'],columns=columns)
