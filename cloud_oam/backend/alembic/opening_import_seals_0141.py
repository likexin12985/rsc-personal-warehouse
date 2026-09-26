"""DDL extension for the unpublished 0141 candidate; no historical rewrite."""
from pathlib import Path
import hashlib
import runpy
from alembic import op
import sqlalchemy as sa

TABLE = 'opening_import_command_seals'
FOLDER = Path(__file__).parent / 'versions'
actor = runpy.run_path(str(FOLDER/'20261105_0126_opening_actor_commit.py'))
ACTOR_BODY = actor['ACTOR_BODY'].replace("AND org_type='region_company') THEN", ") THEN")
ACTOR_BODY = ACTOR_BODY.replace("p.action='manage'", "p.action=p_action")
ACTOR_BODY = ACTOR_BODY.replace("IF (assignment.role_code='admin'", "IF assignment.id=p_assignment AND ((assignment.role_code='admin'")
ACTOR_BODY = ACTOR_BODY.replace("lower(assignment.scope_id)=p_region::text) THEN", "lower(assignment.scope_id)=ANY(path::text[]))) THEN")
ACTOR_BODY = ACTOR_BODY.replace("IF p_user IS NULL", "IF p_assignment IS NULL OR p_action NOT IN ('manage','read') OR p_user IS NULL")
ACTOR = 'rsc_assert_opening_import_seal_actor_0141'
# Same coordinates as formal_files._take_file_advisory_lock. The import-key
# lock follows the file lock everywhere, before task or principal row locks.
FILE_LOCK = "PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to('formal-file:'||file_identifier::text,'UTF8')),'hex'),1,16))::bit(64)::bigint);"
KEY_LOCK = "PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(convert_to('opening-import-admission:'||request_hash,'UTF8')),'hex'),1,16))::bit(64)::bigint);"
ADMISSION = 'rsc_guard_opening_import_admission_0141'
ADMISSION_BODY = f"""
DECLARE file_identifier uuid; request_hash text;
BEGIN
    IF TG_TABLE_NAME='files' THEN
        IF NEW.metadata_jsonb->>'purpose' IS DISTINCT FROM 'opening_count_import' THEN RETURN NEW; END IF;
        file_identifier:=NEW.id;
    ELSE
        IF NEW.job_type<>'import' THEN RETURN NEW; END IF;
        file_identifier:=(NEW.import_binding_jsonb->>'source_file_id')::uuid;
        request_hash:=NEW.idempotency_key;
    END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0141 admission requires READ COMMITTED' USING ERRCODE='25001'; END IF;
    {FILE_LOCK}
    IF request_hash IS NOT NULL THEN {KEY_LOCK} END IF;
    IF EXISTS(SELECT 1 FROM public.{TABLE} WHERE source_file_id=file_identifier OR import_key_hash=request_hash) THEN
        RAISE EXCEPTION '0141 original import permanently sealed' USING ERRCODE='23514',CONSTRAINT='opening_import_sealed_0141';
    END IF;
    RETURN NEW;
END
"""
GUARD = 'rsc_guard_opening_import_seal_0141'
GUARD_BODY = f"""
DECLARE
    file_identifier uuid:=NEW.source_file_id; request_hash text:=NEW.import_key_hash;
    target record; organization uuid; permission_action text; expected jsonb;
BEGIN
    IF session_user<>'star_oam_api' OR current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0141 seal requires API READ COMMITTED' USING ERRCODE='42501'; END IF;
    {FILE_LOCK} {KEY_LOCK}
    PERFORM 1 FROM public.stocktake_tasks WHERE id=NEW.task_id FOR UPDATE;
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY(
        SELECT DISTINCT identifier FROM unnest(ARRAY[NEW.actor_user_id,NEW.reviewer_user_id]::text[]) AS identities(identifier)
        ORDER BY identifier));
    SELECT t.region_org_id,s.owner_org_id,l.owner_org_id AS location_owner INTO target
      FROM public.stocktake_tasks t JOIN public.stocktake_rounds r ON r.task_id=t.id
      JOIN public.stocktake_scopes s ON s.task_id=t.id JOIN public.stock_locations l ON l.id=s.location_id
      WHERE t.id=NEW.task_id AND t.task_type='opening' AND r.id=NEW.round_id AND s.id=NEW.scope_id;
    IF NOT FOUND OR NEW.id='00000000-0000-0000-0000-000000000000'::uuid
       OR NEW.source_file_id='00000000-0000-0000-0000-000000000000'::uuid OR NEW.authorization_version<1 OR NEW.reviewer_authorization_version<1
       OR NEW.source_sha256 !~ '^[0-9a-f]{{64}}$' OR NEW.upload_key_hash !~ '^[0-9a-f]{{64}}$'
       OR NEW.import_key_hash !~ '^[0-9a-f]{{64}}$' OR NEW.size_bytes NOT BETWEEN 1 AND 8388608
       OR NOT isfinite(NEW.created_at) OR NEW.created_at<transaction_timestamp() OR NEW.created_at>clock_timestamp()
       OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=NEW.actor_user_id AND person_id=NEW.actor_person_id
           AND authorization_version>=NEW.authorization_version)
       OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=NEW.reviewer_user_id AND person_id=NEW.reviewer_person_id)
       OR NOT EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
          WHERE a.id=NEW.reviewer_assignment_id AND a.user_id=NEW.reviewer_user_id AND (
            (r.code='admin' AND a.scope_type='national' AND a.scope_id='*') OR
            (r.code='provincial_manager' AND a.scope_type='organization' AND lower(a.scope_id)=target.region_org_id::text))) THEN
        RAISE EXCEPTION '0141 seal coordinates invalid' USING ERRCODE='23514'; END IF;
    FOREACH organization IN ARRAY ARRAY[target.region_org_id,target.owner_org_id,target.location_owner] LOOP
      FOREACH permission_action IN ARRAY ARRAY['manage','read'] LOOP
        PERFORM public.{ACTOR}(NEW.reviewer_user_id,NEW.reviewer_authorization_version,organization,NEW.reviewer_assignment_id,permission_action);
      END LOOP;
    END LOOP;
    IF EXISTS(SELECT 1 FROM public.files f WHERE f.id=NEW.source_file_id AND (
       f.uploaded_by IS DISTINCT FROM NEW.actor_user_id OR f.sha256 IS DISTINCT FROM NEW.source_sha256
       OR f.size_bytes IS DISTINCT FROM NEW.size_bytes OR f.metadata_jsonb->>'purpose' IS DISTINCT FROM 'opening_count_import'
       OR f.metadata_jsonb->>'uploader_person_id' IS DISTINCT FROM NEW.actor_person_id::text
       OR f.metadata_jsonb->>'authorization_version' IS DISTINCT FROM NEW.authorization_version::text
       OR f.metadata_jsonb->>'idempotency_key_hash' IS DISTINCT FROM NEW.upload_key_hash)) THEN
        RAISE EXCEPTION '0141 seal source mismatch' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT 1 FROM public.file_jobs j WHERE j.job_type='import' AND (
       j.idempotency_key=NEW.import_key_hash OR j.import_binding_jsonb->>'source_file_id'=NEW.source_file_id::text)) THEN
        RAISE EXCEPTION '0141 accepted import cannot be sealed' USING ERRCODE='23514',CONSTRAINT='opening_import_accepted_0141'; END IF;
    IF TG_WHEN='AFTER' THEN
      expected:=to_jsonb(NEW)-ARRAY['id','created_at','actor_user_id','reviewer_user_id'];
      IF (SELECT count(*) FROM public.audit_events WHERE aggregate_type='opening_import_command_seal' AND aggregate_id=NEW.id::text)<>1
         OR NOT EXISTS(SELECT 1 FROM public.audit_events WHERE stream_key='inventory'
           AND aggregate_type='opening_import_command_seal' AND aggregate_id=NEW.id::text
           AND action='opening_import.sealed' AND actor_user_id=NEW.reviewer_user_id
           AND request_id='opening-import-seal:'||NEW.id::text AND before_jsonb='{{}}'::jsonb AND after_jsonb=expected
           AND occurred_at=NEW.created_at AND created_at=NEW.created_at
           AND xmin::text::numeric=mod(pg_current_xact_id()::text::numeric,4294967296)) THEN
          RAISE EXCEPTION '0141 seal requires same transaction audit' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END
"""
IMMUTABLE = 'rsc_guard_opening_import_seal_immutable_0141'
IMMUTABLE_BODY = "BEGIN RAISE EXCEPTION '0141 original import seals are immutable' USING ERRCODE='23514'; END"
FUNCTIONS = {
    ACTOR: ('text, bigint, uuid, uuid, text','p_user text,p_version bigint,p_region uuid,p_assignment uuid,p_action text','void',ACTOR_BODY),
    ADMISSION: ('','','trigger',ADMISSION_BODY), GUARD: ('','','trigger',GUARD_BODY), IMMUTABLE: ('','','trigger',IMMUTABLE_BODY),
}
HASHES = {n:hashlib.sha256(v[3].encode()).hexdigest() for n,v in FUNCTIONS.items()}
# Prefix sorts before the existing file/job guard, before it locks principals.
TRIGGERS = {
 'trg_000_opening_import_file_admission_0141':('files','INSERT OR UPDATE',ADMISSION,23,False),
 'trg_000_opening_import_job_admission_0141':('file_jobs','INSERT',ADMISSION,7,False),
 'trg_opening_import_seal_insert_0141':(TABLE,'INSERT',GUARD,7,False),
 'trg_opening_import_seal_commit_0141':(TABLE,'INSERT',GUARD,5,True),
 'trg_opening_import_seal_immutable_0141':(TABLE,'UPDATE OR DELETE',IMMUTABLE,27,False),
 'trg_opening_import_seal_truncate_0141':(TABLE,'TRUNCATE',IMMUTABLE,34,False),
}

def schema(up):
    if not up: op.drop_table(TABLE); return
    fields=[sa.Column('id',sa.Uuid(),primary_key=True)]
    for name in ('actor_user_id','reviewer_user_id'):
        fields.append(sa.Column(name,sa.String(36),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False))
    for name,table in [('actor_person_id','people'),('reviewer_person_id','people'),('reviewer_assignment_id','role_assignments'),
                       ('task_id','stocktake_tasks'),('round_id','stocktake_rounds'),('scope_id','stocktake_scopes')]:
        fields.append(sa.Column(name,sa.Uuid(),sa.ForeignKey(table+'.id',ondelete='RESTRICT'),nullable=False))
    fields.extend([sa.Column('authorization_version',sa.BigInteger(),nullable=False),sa.Column('reviewer_authorization_version',sa.BigInteger(),nullable=False),
        sa.Column('source_file_id',sa.Uuid(),nullable=False),sa.Column('source_sha256',sa.String(64),nullable=False),
        sa.Column('size_bytes',sa.BigInteger(),nullable=False),sa.Column('upload_key_hash',sa.String(64),nullable=False),
        sa.Column('import_key_hash',sa.String(64),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('source_file_id',name='uq_opening_import_seal_source'),sa.UniqueConstraint('import_key_hash',name='uq_opening_import_seal_request'),
        sa.CheckConstraint('authorization_version>0 AND reviewer_authorization_version>0 AND size_bytes BETWEEN 1 AND 8388608',name='ck_opening_import_seal_size_version')])
    op.create_table(TABLE,*fields)

def triggers(up):
    if op.get_bind().dialect.name=='sqlite':
        if up:
            op.execute(f"CREATE TRIGGER trg_opening_import_seal_pg16 BEFORE INSERT ON {TABLE} BEGIN SELECT RAISE(ABORT,'0141 seals require PostgreSQL 16'); END")
        return
    if up:
        for name,(args,params,returns,body) in FUNCTIONS.items():
            op.execute(f'CREATE FUNCTION public.{name}({params}) RETURNS {returns} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $seal${body}$seal$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{name}({args}) FROM PUBLIC,star_oam_api')
        for name,(table,events,function,_,deferred) in TRIGGERS.items():
            sql=(f'CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW' if deferred else
                 f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}")
            op.execute(sql+f' EXECUTE FUNCTION public.{function}()'); op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
        op.execute(f'REVOKE ALL ON TABLE public.{TABLE} FROM PUBLIC,star_oam_api')
        op.execute(f'GRANT SELECT,INSERT ON TABLE public.{TABLE} TO star_oam_api')
    else:
        for name,(table,*_) in reversed(tuple(TRIGGERS.items())): op.execute(f'DROP TRIGGER {name} ON public.{table}')
        for name,(args,*_) in reversed(tuple(FUNCTIONS.items())): op.execute(f'DROP FUNCTION public.{name}({args})')
