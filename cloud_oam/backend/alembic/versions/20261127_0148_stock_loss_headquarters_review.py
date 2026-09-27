"""Immutable headquarters loss decisions; approval never releases stock."""
from pathlib import Path
import hashlib
import runpy

from alembic import op
import sqlalchemy as sa

revision = '20261127_0148'
down_revision = '20261126_0147'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER/'20261126_0147_stock_loss_regional_review.py'))
OLD_READY_HASH = previous['NEW_READY_HASH']
ready = previous['ready']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()
TABLE = 'stock_loss_headquarters_reviews'
LINES = 'stock_loss_headquarters_decisions'

AUTHORITY_BODY = """
DECLARE actor public.users%ROWTYPE; person public.people%ROWTYPE; actor_org public.organizations%ROWTYPE;
    checked_at timestamptz; ancestors uuid[]; latest uuid[]; invalid_tree boolean;
BEGIN
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[actor_id]::text[]);
    SELECT * INTO actor FROM public.users WHERE id=actor_id;
    SELECT * INTO person FROM public.people WHERE id=actor.person_id;
    SELECT * INTO actor_org FROM public.organizations WHERE id=person.organization_id FOR SHARE;
    IF actor.id IS NULL OR person.id IS NULL OR actor_org.id IS NULL
       OR NOT actor.is_active OR actor.account_status<>'active' OR person.employment_status<>'active'
       OR actor.person_id IS DISTINCT FROM person_id OR actor.authorization_version IS DISTINCT FROM actor_version
       OR actor_org.status<>'active' OR actor_org.org_type NOT IN ('headquarters','region_company','department')
       OR NOT EXISTS(SELECT 1 FROM public.auth_identities i WHERE i.user_id=actor.id
            AND i.status='active' AND i.verified_at IS NOT NULL AND i.revoked_at IS NULL) THEN
        RAISE EXCEPTION '0148 current headquarters reviewer identity invalid' USING ERRCODE='23514'; END IF;
    WITH RECURSIVE tree AS (
        SELECT id,parent_id,status,ARRAY[id] visited,false cycle FROM public.organizations WHERE id=owner_id
        UNION ALL SELECT o.id,o.parent_id,o.status,t.visited||o.id,o.id=ANY(t.visited)
            FROM public.organizations o JOIN tree t ON o.id=t.parent_id WHERE NOT t.cycle
    ) SELECT array_agg(id ORDER BY id),bool_or(cycle OR status<>'active') INTO ancestors,invalid_tree FROM tree;
    IF COALESCE(invalid_tree,true) THEN
        RAISE EXCEPTION '0148 headquarters owner tree invalid' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.organizations WHERE id=ANY(ancestors)
       OR id::text IN (SELECT scope_id FROM public.role_assignments WHERE user_id=actor_id AND scope_type='organization')
       ORDER BY id FOR SHARE;
    WITH RECURSIVE tree AS (
        SELECT id,parent_id,status,ARRAY[id] visited,false cycle FROM public.organizations WHERE id=owner_id
        UNION ALL SELECT o.id,o.parent_id,o.status,t.visited||o.id,o.id=ANY(t.visited)
            FROM public.organizations o JOIN tree t ON o.id=t.parent_id WHERE NOT t.cycle
    ) SELECT array_agg(id ORDER BY id),bool_or(cycle OR status<>'active') INTO latest,invalid_tree FROM tree;
    IF COALESCE(invalid_tree,true) OR ancestors IS DISTINCT FROM latest
       OR NOT EXISTS(SELECT 1 FROM public.organizations WHERE id=owner_id AND org_type='region_company' AND status='active') THEN
        RAISE EXCEPTION '0148 headquarters owner changed' USING ERRCODE='23514'; END IF;
    checked_at:=clock_timestamp();
    IF EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        LEFT JOIN public.organizations scope ON scope.id::text=a.scope_id
        WHERE a.user_id=actor.id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=checked_at AND (a.valid_to IS NULL OR a.valid_to>checked_at) AND r.status='active'
          AND NOT CASE r.code
            WHEN 'admin' THEN NOT r.is_external AND a.scope_type='national' AND a.scope_id='*' AND actor_org.org_type='headquarters'
            WHEN 'provincial_manager' THEN NOT r.is_external AND a.scope_type='organization' AND scope.id IS NOT NULL
                AND scope.org_type='region_company' AND scope.status='active'
            WHEN 'technician' THEN NOT r.is_external AND a.scope_type='person' AND a.scope_id=person.id::text
            ELSE false END) THEN
        RAISE EXCEPTION '0148 headquarters reviewer graph invalid' USING ERRCODE='23514'; END IF;
    IF NOT EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=actor.id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=checked_at AND (a.valid_to IS NULL OR a.valid_to>checked_at)
          AND r.status='active' AND r.code='admin' AND NOT r.is_external AND actor_org.org_type='headquarters'
          AND a.scope_type='national' AND a.scope_id='*'
          AND rp.effect='allow' AND p.resource='stock_operation' AND p.action='finalize_loss' AND p.field_code='')
       OR EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=actor.id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=checked_at AND (a.valid_to IS NULL OR a.valid_to>checked_at) AND r.status='active'
          AND rp.effect='deny' AND p.resource='stock_operation' AND p.action='finalize_loss' AND p.field_code=''
          AND ((a.scope_type='national' AND a.scope_id='*')
            OR (a.scope_type='organization' AND a.scope_id IN (SELECT value::text FROM unnest(ancestors) value)))) THEN
        RAISE EXCEPTION '0148 current headquarters review authority required' USING ERRCODE='23514'; END IF;
END;
"""

LOCK_BODY = """
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0148 headquarters review requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0148 inventory ledger required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""

CHECK_BODY = """
DECLARE review public.stock_loss_headquarters_reviews%ROWTYPE; parent public.stock_operation_orders%ROWTYPE;
    location public.stock_locations%ROWTYPE; regional public.stock_loss_regional_reviews%ROWTYPE;
    account_id uuid; body jsonb; command jsonb; decisions jsonb;
    aggregate text:='stock_loss_headquarters_review'; kind text:='stock_loss.headquarters_approved';
BEGIN
    SELECT * INTO review FROM public.stock_loss_headquarters_reviews WHERE id=checked_review;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=review.operation_id;
    SELECT * INTO location FROM public.stock_locations WHERE id=parent.source_location_id FOR SHARE;
    SELECT * INTO regional FROM public.stock_loss_regional_reviews WHERE id=review.regional_review_id;
    IF review.id IS NULL OR parent.id IS NULL OR location.id IS NULL OR regional.id IS NULL
       OR parent.operation_type<>'loss_report' OR parent.status<>'submitted' OR review.operation_type<>'loss_report'
       OR review.owner_org_id<>location.owner_org_id OR review.submission_plan_hash<>parent.plan_hash
       OR review.actor_user_id=parent.actor_user_id OR review.reviewer_person_id=parent.requester_id
       OR review.decision<>'approved'
       OR regional.operation_id<>parent.id OR regional.operation_type<>'loss_report'
       OR regional.owner_org_id<>review.owner_org_id OR regional.submission_plan_hash<>parent.plan_hash
       OR regional.decision<>'verified' OR regional.request_hash<>review.regional_review_hash
       OR regional.created_at>review.created_at OR review.authorization_version<1
       OR length(trim(review.comment)) NOT BETWEEN 1 AND 1000 OR trim(review.comment)<>review.comment
       OR review.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
       OR review.idempotency_key_hash !~ '^[a-f0-9]{64}$' OR review.request_hash !~ '^[a-f0-9]{64}$'
       OR review.submission_plan_hash !~ '^[a-f0-9]{64}$'
       OR review.created_at<parent.created_at OR review.created_at>clock_timestamp()
       OR NOT EXISTS(SELECT 1 FROM public.inventory_transactions t WHERE t.id=parent.posting_transaction_id
            AND t.status='posted' AND t.movement_type='freeze' AND t.source_document_type='stock_operation_loss'
            AND t.source_document_id=parent.id::text)
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id=parent.posting_transaction_id)
       OR NOT EXISTS(SELECT 1 FROM public.stock_operation_lines WHERE operation_id=parent.id)
       OR EXISTS(SELECT 1 FROM public.stock_operation_lines l JOIN public.stock_accounts a ON a.id=l.reserved_account_id
            WHERE l.operation_id=parent.id AND (l.operation_type<>'loss_report' OR a.owner_org_id<>review.owner_org_id)) THEN
        RAISE EXCEPTION '0148 exact independent headquarters loss review required' USING ERRCODE='23514'; END IF;
    IF (SELECT count(*) FROM public.stock_loss_headquarters_decisions WHERE review_id=review.id) NOT BETWEEN 1 AND 100
       OR EXISTS(SELECT 1 FROM public.stock_operation_lines l WHERE l.operation_id=parent.id
            AND NOT EXISTS(SELECT 1 FROM public.stock_loss_headquarters_decisions d WHERE d.review_id=review.id AND d.line_id=l.id))
       OR EXISTS(SELECT 1 FROM public.stock_loss_headquarters_decisions d
            JOIN public.stock_operation_lines l ON l.id=d.line_id WHERE d.review_id=review.id
            AND (l.operation_id<>parent.id OR l.operation_type<>'loss_report' OR d.created_at<>review.created_at
                OR d.disposition NOT IN ('restore_available','convert_used','convert_damaged','return_to_region','scrap')
                OR length(trim(d.reason)) NOT BETWEEN 1 AND 500 OR trim(d.reason)<>d.reason)) THEN
        RAISE EXCEPTION '0148 exact complete headquarters line decisions required' USING ERRCODE='23514'; END IF;
    SELECT jsonb_agg(jsonb_build_object('line_id',line_id::text,'disposition',disposition,'reason',reason) ORDER BY line_id)
        INTO decisions FROM public.stock_loss_headquarters_decisions WHERE review_id=review.id;
    PERFORM public.rsc_assert_loss_headquarters_authority_0148(review.actor_user_id,review.authorization_version,
        review.reviewer_person_id,review.owner_org_id);
    -- Historical submission authority is not re-required: a departure must
    -- not stop a current headquarters reviewer from handling the frozen report.
    FOR account_id IN SELECT DISTINCT reserved_account_id FROM public.stock_operation_lines
        WHERE operation_id=parent.id ORDER BY reserved_account_id LOOP
        PERFORM public.rsc_check_loss_hold_0145(account_id);
    END LOOP;
    command:=jsonb_build_object('operation_id',parent.id::text,
        'expected_submission_plan_hash',parent.plan_hash,'comment',review.comment,
        'regional_review_id',review.regional_review_id::text,'expected_regional_review_hash',review.regional_review_hash,
        'decisions',decisions);
    IF review.request_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(command),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0148 headquarters review request hash mismatch' USING ERRCODE='23514'; END IF;
    body:=jsonb_build_object('review_id',review.id::text,'operation_id',parent.id::text,'owner_org_id',review.owner_org_id::text,
        'reviewer_person_id',review.reviewer_person_id::text,'authorization_version',review.authorization_version,
        'decision',review.decision,'comment',review.comment,'request_id',review.request_id,'request_hash',review.request_hash,
        'submission_plan_hash',review.submission_plan_hash,'regional_review_id',review.regional_review_id::text,
        'regional_review_hash',review.regional_review_hash,'decisions',decisions,'approval_stage','approved','stock_effect','none');
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='inventory' AND aggregate_type=aggregate AND aggregate_id=review.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.audit_events e WHERE e.stream_key='inventory' AND e.aggregate_type=aggregate
            AND e.aggregate_id=review.id::text AND e.actor_user_id=review.actor_user_id AND e.action=kind
            AND e.request_id='stock-loss-headquarters-review:'||review.id::text AND e.before_jsonb='{}'::jsonb
            AND e.after_jsonb=body AND e.occurred_at=review.created_at AND e.created_at=review.created_at)
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type=aggregate AND aggregate_id=review.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.state_transition_events e WHERE e.aggregate_type=aggregate AND e.aggregate_id=review.id::text
            AND e.from_status='awaiting_headquarters' AND e.to_status='approved' AND e.actor_id=review.actor_user_id
            AND e.reason=kind AND e.idempotency_key=kind||':'||review.id::text AND e.metadata_jsonb=body
            AND e.occurred_at=review.created_at AND e.created_at=review.created_at)
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type=aggregate AND aggregate_id=review.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.outbox_events e WHERE e.aggregate_type=aggregate AND e.aggregate_id=review.id::text
            AND e.event_type=kind AND e.idempotency_key=kind||':'||review.id::text AND e.payload_jsonb=body
            AND e.created_at=review.created_at) THEN
        RAISE EXCEPTION '0148 complete headquarters review audit state outbox required' USING ERRCODE='23514'; END IF;
    IF (SELECT count(*) FROM public.notification_events WHERE business_type=aggregate AND business_id=review.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.notification_events n WHERE n.business_type=aggregate AND n.business_id=review.id::text
            AND n.event_type=kind AND n.dedup_key=kind||':'||review.id::text AND n.payload_jsonb=body
            AND n.target_manifest_sha256=encode(sha256(convert_to('notification-person-targets.v1'||chr(10)||parent.requester_id::text,'UTF8')),'hex')
            AND (SELECT count(*) FROM public.notification_person_targets WHERE event_id=n.id)=1
            AND EXISTS(SELECT 1 FROM public.notification_person_targets WHERE event_id=n.id AND person_id=parent.requester_id)) THEN
        RAISE EXCEPTION '0148 independent headquarters review notification required' USING ERRCODE='23514'; END IF;
END;
"""

GUARD_BODY = """
DECLARE identifier uuid; event public.notification_events%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='inventory_transactions' THEN
        IF NEW.source_document_type IN ('stock_loss_headquarters_review','stock_loss_headquarters_decision') THEN
            RAISE EXCEPTION '0148 headquarters review cannot post stock' USING ERRCODE='23514'; END IF;
        RETURN NULL;
    ELSIF TG_TABLE_NAME='stock_loss_headquarters_reviews' THEN identifier:=NEW.id;
    ELSIF TG_TABLE_NAME='stock_loss_headquarters_decisions' THEN identifier:=NEW.review_id;
    ELSIF TG_TABLE_NAME IN ('notification_events','notification_person_targets') THEN
        IF TG_TABLE_NAME='notification_events' THEN event:=NEW;
        ELSE SELECT * INTO event FROM public.notification_events WHERE id=NEW.event_id; END IF;
        IF event.business_type<>'stock_loss_headquarters_review' AND event.event_type<>'stock_loss.headquarters_approved' THEN RETURN NULL; END IF;
        IF event.business_type<>'stock_loss_headquarters_review' OR event.event_type<>'stock_loss.headquarters_approved' THEN
            RAISE EXCEPTION '0148 detached headquarters review notification' USING ERRCODE='23514'; END IF;
        identifier:=event.business_id::uuid;
    ELSE
        IF NEW.aggregate_type<>'stock_loss_headquarters_review' THEN RETURN NULL; END IF;
        identifier:=NEW.aggregate_id::uuid;
    END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0148 headquarters proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0148 inventory ledger required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_loss_headquarters_review_0148(identifier);
    RETURN NULL;
END;
"""

FUNCTIONS = {
    ('rsc_assert_loss_headquarters_authority_0148','text, bigint, uuid, uuid'):
        ('actor_id text, actor_version bigint, person_id uuid, owner_id uuid','void',AUTHORITY_BODY),
    ('rsc_lock_loss_headquarters_review_0148',''):('','trigger',LOCK_BODY),
    ('rsc_check_loss_headquarters_review_0148','uuid'):('checked_review uuid','void',CHECK_BODY),
    ('rsc_guard_loss_headquarters_review_0148',''):('','trigger',GUARD_BODY),
}
FUNCTION_HASHES = {key:hashlib.sha256(value[2].encode()).hexdigest() for key,value in FUNCTIONS.items()}
TRIGGERS = {f'trg_{table}_loss_headquarters_0148':(table,'INSERT','rsc_guard_loss_headquarters_review_0148',5,True)
    for table in (TABLE,LINES,'audit_events','state_transition_events','outbox_events','notification_events','notification_person_targets','inventory_transactions')}
for table, prefix in ((TABLE,'review'),(LINES,'decision')):
    TRIGGERS.update({
        f'trg_loss_hq_{prefix}_lock_0148':(table,'INSERT','rsc_lock_loss_headquarters_review_0148',7,False),
        f'trg_loss_hq_{prefix}_immutable_0148':(table,'UPDATE OR DELETE','rsc_guard_work_order_facts_0090',27,False),
        f'trg_loss_hq_{prefix}_truncate_0148':(table,'TRUNCATE','rsc_guard_work_order_facts_0090',34,False),
    })


def _schema():
    op.create_table(TABLE,
        sa.Column('id',sa.Uuid(),primary_key=True,nullable=False),
        sa.Column('operation_id',sa.Uuid(),nullable=False),
        sa.Column('operation_type',sa.String(24),nullable=False),
        sa.Column('owner_org_id',sa.Uuid(),sa.ForeignKey('organizations.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('reviewer_person_id',sa.Uuid(),sa.ForeignKey('people.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('authorization_version',sa.BigInteger(),nullable=False),
        sa.Column('regional_review_id',sa.Uuid(),sa.ForeignKey('stock_loss_regional_reviews.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('regional_review_hash',sa.String(64),nullable=False),
        sa.Column('decision',sa.String(24),nullable=False),
        sa.Column('comment',sa.Text(),nullable=False),
        sa.Column('request_id',sa.String(160),nullable=False),
        sa.Column('idempotency_key_hash',sa.String(64),nullable=False),
        sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('submission_plan_hash',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(['operation_id','operation_type'],['stock_operation_orders.id','stock_operation_orders.operation_type'],
            name='fk_loss_headquarters_review_typed_parent',ondelete='RESTRICT'),
        sa.UniqueConstraint('operation_id',name='uq_loss_headquarters_review_order'),
        sa.UniqueConstraint('regional_review_id',name='uq_loss_headquarters_review_regional'),
        sa.UniqueConstraint('actor_user_id','request_id',name='uq_loss_headquarters_review_request'),
        sa.UniqueConstraint('idempotency_key_hash',name='uq_loss_headquarters_review_key'),
        sa.CheckConstraint("operation_type='loss_report' AND decision='approved'",name='ck_loss_headquarters_review_kind'),
        sa.CheckConstraint('authorization_version>0 AND length(comment) BETWEEN 1 AND 1000',name='ck_loss_headquarters_review_context'),
        sa.CheckConstraint('length(regional_review_hash)=64 AND length(request_hash)=64 AND length(submission_plan_hash)=64 AND length(idempotency_key_hash)=64',name='ck_loss_headquarters_review_hashes'),
        sa.CheckConstraint('length(request_id) BETWEEN 8 AND 160',name='ck_loss_headquarters_review_request'))

    op.create_table(LINES,
        sa.Column('id',sa.Uuid(),primary_key=True,nullable=False),
        sa.Column('review_id',sa.Uuid(),sa.ForeignKey(TABLE+'.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('line_id',sa.Uuid(),sa.ForeignKey('stock_operation_lines.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('disposition',sa.String(24),nullable=False),
        sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('review_id','line_id',name='uq_loss_headquarters_decision_line'),
        sa.CheckConstraint("disposition IN ('restore_available','convert_used','convert_damaged','return_to_region','scrap')",name='ck_loss_headquarters_disposition'),
        sa.CheckConstraint('length(reason) BETWEEN 1 AND 500',name='ck_loss_headquarters_reason'))


def _transition(up):
    helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect=op.get_bind().dialect.name
    if dialect not in ('postgresql','sqlite'):raise RuntimeError('0148 requires PostgreSQL or SQLite')
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0148 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        tables=tuple(dict.fromkeys(t[0] for t in TRIGGERS.values() if t[0] not in (TABLE,LINES)))
        op.execute('LOCK TABLE public.inventory_ledger_heads,public.stock_operation_orders,public.stock_operation_lines,public.stock_loss_regional_reviews,'+
            ','.join('public.'+table for table in tables)+' IN SHARE ROW EXCLUSIVE MODE')
        if not up:op.execute('LOCK TABLE public.stock_loss_headquarters_reviews,public.stock_loss_headquarters_decisions IN ACCESS EXCLUSIVE MODE')
    if not up:helper['_preflight']('EXISTS(SELECT 1 FROM stock_loss_headquarters_reviews) OR EXISTS(SELECT 1 FROM stock_loss_headquarters_decisions)',
        '0148 immutable headquarters review history requires retention')
    if up:_schema()
    if dialect=='postgresql':
        if up:
            for (name,signature),(args,result,body) in FUNCTIONS.items():
                op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${body}$body$')
                op.execute(f'REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC,star_oam_api')
            for name,(table,events,function,_,deferred) in TRIGGERS.items():
                statement=(f'CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW'
                    if deferred else f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}")
                op.execute(statement+f' EXECUTE FUNCTION public.{function}()')
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
            op.execute(f'GRANT SELECT,INSERT ON public.{TABLE},public.{LINES} TO star_oam_api')
        for (name,signature),(args,result,body) in FUNCTIONS.items():
            previous['previous']['_verify_function'](name,signature,args,result,body)
        replace=runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
            replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='loss_headquarters_ready_0148')
        if not up:
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name,signature in reversed(FUNCTIONS):op.execute(f'DROP FUNCTION public.{name}({signature})')
    elif up:
        for table,prefix in ((TABLE,'review'),(LINES,'decision')):
            op.execute(f"CREATE TRIGGER trg_loss_hq_{prefix}_insert_0148 BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'0148 PostgreSQL headquarters proof required'); END")
            for event in ('UPDATE','DELETE'):
                op.execute(f"CREATE TRIGGER trg_loss_hq_{prefix}_{event.lower()}_0148 BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'0148 immutable headquarters reviews'); END")
    if not up:
        op.drop_table(LINES)
        op.drop_table(TABLE)


def upgrade():_transition(True)
def downgrade():_transition(False)
