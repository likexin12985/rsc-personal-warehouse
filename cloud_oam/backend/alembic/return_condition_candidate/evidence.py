"""Candidate immutable event/file proof and exclusive purpose boundaries.

Compiled over the complete 0165 model; not a published migration. It needs
condition tables and the canonical 0026 JSON function. Completed upload
admission is separate, as are physical verification and current action rights.
"""
from pathlib import Path
import runpy

from app.return_condition_schema import build_schema

PURPOSE = 'return_condition_evidence'


def references():
    metadata, _, _ = build_schema()
    # Include untyped legacy UUID references as well as actual foreign keys.
    # The final migration must freeze and verify this complete source catalog.
    result = {}
    for table in metadata.tables.values():
        if table.name == 'stock_condition_files':
            continue
        columns = tuple(sorted(c.name for c in table.c
            if c.name == 'file_id' or c.name.endswith('_file_id')
            or any(f.target_fullname == 'files.id' for f in c.foreign_keys)))
        if columns:
            result[table.name] = columns
    return dict(sorted(result.items()))


def statements():
    old = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'versions'
                            / '20260901_0036_formal_file_runtime_boundary.py'))
    shape = old['_postgresql_file_shape_sql']('file_row', status='available', require_current_identity=False)
    assert shape.count(old['FORMAL_PURPOSES_SQL']) == 1
    shape = shape.replace(old['FORMAL_PURPOSES_SQL'], "('return_condition_evidence')")
    foreign = references()
    reused = ' OR\n'.join('EXISTS (SELECT 1 FROM public.'+table+
        ' WHERE '+ ' OR '.join(column+'=checked_file' for column in columns)+')'
        for table, columns in foreign.items())
    # Daily-review manifests predate a typed file-link table. Preserve their
    # scope/purpose guards and cover them in this new purpose's reverse proof.
    reused += """ OR EXISTS (SELECT 1 FROM public.daily_review_events d,
        LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(d.payload_jsonb->'evidence')='array'
            THEN d.payload_jsonb->'evidence' ELSE '[]'::jsonb END) AS e
        WHERE e->>'file_id'=checked_file::text)"""
    file_body = f"""
DECLARE file_row public.files%ROWTYPE; binding_row public.stock_condition_files%ROWTYPE;
        event_row public.stock_condition_events%ROWTYPE;
BEGIN
    SELECT * INTO file_row FROM public.files WHERE id=checked_file FOR SHARE;
    SELECT * INTO binding_row FROM public.stock_condition_files WHERE file_id=checked_file;
    IF file_row.id IS NULL THEN
        IF binding_row.file_id IS NOT NULL THEN
            RAISE EXCEPTION 'condition bound evidence file missing' USING ERRCODE='23514';
        END IF;
        RETURN;
    END IF;
    IF file_row.metadata_jsonb->>'purpose' IS DISTINCT FROM '{PURPOSE}' AND binding_row.file_id IS NULL THEN RETURN; END IF;
    IF {reused} THEN
        RAISE EXCEPTION 'condition evidence has a foreign binding' USING ERRCODE='23514';
    END IF;
    IF binding_row.file_id IS NULL THEN RETURN; END IF;
    IF jsonb_typeof(file_row.metadata_jsonb) IS DISTINCT FROM 'object'
        OR jsonb_typeof(file_row.metadata_jsonb->'completion') IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'condition completed evidence metadata required' USING ERRCODE='23514';
    END IF;
    SELECT * INTO event_row FROM public.stock_condition_events WHERE id=binding_row.event_id;
    IF event_row.id IS NULL OR NOT COALESCE(({shape}),false)
        OR file_row.uploaded_by IS DISTINCT FROM event_row.actor_user_id
        OR file_row.metadata_jsonb->>'uploader_person_id' IS DISTINCT FROM event_row.actor_person_id::text
        OR file_row.metadata_jsonb->'authorization_version' IS DISTINCT FROM to_jsonb(event_row.authorization_version)
        OR binding_row.created_at IS DISTINCT FROM event_row.created_at
        OR NOT COALESCE((file_row.metadata_jsonb->'completion'->>'verified_at') ~ '(Z|[+-][0-9]{{2}}:[0-9]{{2}})$',false)
        OR (file_row.metadata_jsonb->'completion'->>'verified_at')::timestamptz > event_row.created_at
        OR binding_row.metadata_sha256 IS DISTINCT FROM encode(sha256(convert_to(
            public.rsc_canonical_reconciliation_json_0026(file_row.metadata_jsonb),'UTF8')),'hex')
        OR file_row.metadata_jsonb->>'request_sha256' IS DISTINCT FROM encode(sha256(convert_to(
            public.rsc_canonical_reconciliation_json_0026(jsonb_build_object('purpose','{PURPOSE}',
                'original_filename',file_row.original_filename,'mime_type',file_row.mime_type,
                'size_bytes',file_row.size_bytes,'sha256',file_row.sha256)),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'condition exact completed event evidence required' USING ERRCODE='23514';
    END IF;
END;
"""
    event_body = """
DECLARE event_row public.stock_condition_events%ROWTYPE; expected jsonb; identifier uuid; total bigint;
BEGIN
    SELECT * INTO event_row FROM public.stock_condition_events WHERE id=checked_event;
    IF event_row.id IS NULL THEN RAISE EXCEPTION 'condition evidence event missing' USING ERRCODE='23514'; END IF;
    SELECT count(*), COALESCE(jsonb_agg(jsonb_build_object('file_id',file_id::text,'metadata_sha256',metadata_sha256)
        ORDER BY file_id::text),'[]'::jsonb) INTO total,expected FROM public.stock_condition_files WHERE event_id=checked_event;
    IF total>20 OR (event_row.kind IN ('submit','supplement','verify_region') AND total=0)
        OR event_row.command_jsonb->'evidence' IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'condition event evidence manifest mismatch' USING ERRCODE='23514';
    END IF;
    FOR identifier IN SELECT file_id FROM public.stock_condition_files WHERE event_id=checked_event ORDER BY file_id LOOP
        PERFORM public.rsc_condition_check_file(identifier);
    END LOOP;
END;
"""
    fence_body = """
DECLARE old_row jsonb; new_row jsonb; identifier uuid;
BEGIN
    IF TG_OP<>'INSERT' THEN old_row:=to_jsonb(OLD); END IF;
    IF TG_OP<>'DELETE' THEN new_row:=to_jsonb(NEW); END IF;
    IF TG_TABLE_NAME='files' THEN
        FOR identifier IN SELECT DISTINCT value::uuid FROM unnest(ARRAY[old_row->>'id',new_row->>'id']) value
            WHERE value IS NOT NULL ORDER BY 1 LOOP
            PERFORM public.rsc_condition_check_file(identifier);
        END LOOP;
    ELSE
        FOR identifier IN SELECT DISTINCT value::uuid FROM unnest(CASE WHEN TG_TABLE_NAME='stock_condition_files'
            THEN ARRAY[old_row->>'event_id',new_row->>'event_id'] ELSE ARRAY[old_row->>'id',new_row->>'id'] END) value
            WHERE value IS NOT NULL ORDER BY 1 LOOP
            PERFORM public.rsc_condition_check_event_files(identifier);
        END LOOP;
    END IF;
    RETURN NULL;
END;
"""
    foreign_body = f"""
DECLARE identifier uuid; ids uuid[];
BEGIN
    IF TG_TABLE_NAME='daily_review_events' THEN
        SELECT array_agg(DISTINCT (value->>'file_id')::uuid ORDER BY (value->>'file_id')::uuid) INTO ids
          FROM jsonb_array_elements(CASE WHEN jsonb_typeof(NEW.payload_jsonb->'evidence')='array'
            THEN NEW.payload_jsonb->'evidence' ELSE '[]'::jsonb END) value WHERE value->>'file_id' IS NOT NULL;
    ELSE
        SELECT array_agg(DISTINCT (to_jsonb(NEW)->>column_name)::uuid ORDER BY (to_jsonb(NEW)->>column_name)::uuid) INTO ids
          FROM unnest(TG_ARGV) column_name WHERE to_jsonb(NEW)->>column_name IS NOT NULL;
    END IF;
    FOREACH identifier IN ARRAY COALESCE(ids,ARRAY[]::uuid[]) LOOP
        PERFORM id FROM public.files WHERE id=identifier FOR SHARE;
        IF EXISTS(SELECT 1 FROM public.files WHERE id=identifier AND metadata_jsonb->>'purpose'='{PURPOSE}') THEN
            RAISE EXCEPTION 'condition evidence reserved for condition events' USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN NEW;
END;
"""
    functions = [('rsc_condition_check_file','checked_file uuid','uuid','void',file_body),
        ('rsc_condition_check_event_files','checked_event uuid','uuid','void',event_body),
        ('rsc_condition_evidence_fence','','','trigger',fence_body),
        ('rsc_condition_foreign_evidence','','','trigger',foreign_body)]
    result=[]
    for name, declaration, signature, returns, body in functions:
        result.extend([f'CREATE FUNCTION public.{name}({declaration}) RETURNS {returns} LANGUAGE plpgsql '
            f'SECURITY DEFINER SET search_path=pg_catalog,public AS $body${body}$body$',
            f'REVOKE ALL ON FUNCTION public.{name}({signature}) FROM '
            'PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup'])
    for table in ('files','stock_condition_files','stock_condition_events'):
        result.extend([f'CREATE CONSTRAINT TRIGGER condition_evidence_complete AFTER INSERT OR UPDATE OR DELETE ON public.{table} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_evidence_fence()',
            f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER condition_evidence_complete'])
    for table, columns in (*foreign.items(),('daily_review_events',())):
        args=','.join("'"+c+"'" for c in columns)
        result.extend([f'CREATE TRIGGER condition_evidence_exclusive BEFORE INSERT OR UPDATE ON public.{table} '
            f'FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_foreign_evidence({args})',
            f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER condition_evidence_exclusive'])
    return result
