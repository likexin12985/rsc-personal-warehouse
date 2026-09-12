"""Immutable whole work-order compensation, reservation and serial proofs."""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261008_0098"
down_revision = "20261007_0097"
branch_labels = depends_on = None
OLD_HASH = "7d8e425310139aed3ce2da6a5344ce8cabb37f3b4555b58c4933b7e307b0d62a"
NEW_HASH = "c3b1e7dbd15f5e98e0e73820fce6d57a14fa7c4be2bcc7029e861f9b825bd61c"
TABLES = ("work_order_reversals", "work_order_reversal_items")

RESERVATION_BODY = """
BEGIN
    IF EXISTS (
        SELECT 1 FROM (
            SELECT sum(CASE WHEN previous.movement_type = 'reserve' OR
                    (previous.movement_type = 'reversal' AND original.movement_type IN ('consume','release'))
                THEN movement.quantity ELSE -movement.quantity END)
                OVER (PARTITION BY CASE WHEN previous.movement_type = 'reserve' OR
                    (previous.movement_type = 'reversal' AND original.movement_type IN ('consume','release'))
                    THEN movement.to_account_id ELSE movement.from_account_id END
                    ORDER BY previous.ledger_cursor, movement.line_no ROWS UNBOUNDED PRECEDING) AS remaining
            FROM public.inventory_transactions previous
            JOIN public.inventory_movements movement ON movement.transaction_id = previous.id
            LEFT JOIN public.inventory_transactions original ON original.id = previous.reversed_transaction_id
            WHERE previous.source_document_type = 'work_order_material' AND previous.source_document_id = checked_order
              AND previous.status = 'posted' AND previous.ledger_cursor <= checked_cursor
              AND (previous.movement_type IN ('reserve','consume','release') OR
                  (previous.movement_type = 'reversal' AND original.movement_type IN ('reserve','consume','release')))
        ) history WHERE remaining < 0
    ) THEN RAISE EXCEPTION '0098 work order reserved quantity exhausted' USING ERRCODE = '23514'; END IF;
    IF EXISTS (
        SELECT 1 FROM (
            SELECT sum(CASE WHEN previous.movement_type = 'reserve' OR
                    (previous.movement_type = 'reversal' AND original.movement_type IN ('consume','release'))
                THEN 1 ELSE -1 END)
                OVER (PARTITION BY sn.serial_id, CASE WHEN previous.movement_type = 'reserve' OR
                    (previous.movement_type = 'reversal' AND original.movement_type IN ('consume','release'))
                    THEN movement.to_account_id ELSE movement.from_account_id END
                    ORDER BY previous.ledger_cursor, movement.line_no ROWS UNBOUNDED PRECEDING) AS remaining
            FROM public.inventory_transactions previous
            JOIN public.inventory_movements movement ON movement.transaction_id = previous.id
            JOIN public.inventory_movement_serials sn ON sn.movement_id = movement.id
            LEFT JOIN public.inventory_transactions original ON original.id = previous.reversed_transaction_id
            WHERE previous.source_document_type = 'work_order_material' AND previous.source_document_id = checked_order
              AND previous.status = 'posted' AND previous.ledger_cursor <= checked_cursor
              AND (previous.movement_type IN ('reserve','consume','release') OR
                  (previous.movement_type = 'reversal' AND original.movement_type IN ('reserve','consume','release')))
        ) history WHERE remaining NOT IN (0,1)
    ) THEN RAISE EXCEPTION '0098 serial reservation does not belong to this work order' USING ERRCODE = '23514'; END IF;
END;
"""

CHECK_BODY = """
DECLARE
    parent public.work_order_reversals%ROWTYPE;
    pair public.work_order_replacements%ROWTYPE;
    item public.work_order_reversal_items%ROWTYPE;
    original public.work_order_material_operations%ROWTYPE;
    inverse public.work_order_material_operations%ROWTYPE;
    original_tx public.inventory_transactions%ROWTYPE;
    inverse_tx public.inventory_transactions%ROWTYPE;
    expected_originals uuid[];
    digest text;
    intent jsonb;
    movements jsonb;
    stock_movements jsonb;
    command jsonb;
    inventory_command jsonb;
    payload jsonb;
    children jsonb := '[]'::jsonb;
    idx integer := 0;
BEGIN
    SELECT * INTO parent FROM public.work_order_reversals WHERE id = checked_reversal;
    IF NOT FOUND THEN RAISE EXCEPTION '0098 reversal command missing' USING ERRCODE = '23514'; END IF;
    intent := jsonb_build_object('work_order_id', parent.oam_work_order_id::text,
        'operator_person_id', parent.operator_person_id::text, 'original_operation_id', parent.original_operation_id::text,
        'original_replacement_id', parent.original_replacement_id::text, 'reason', parent.reason);
    IF parent.reversal_no <> 'WOV-' || upper(substr(parent.idempotency_key_hash,1,24))
       OR parent.idempotency_key_hash !~ '^[0-9a-f]{64}$' OR parent.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
       OR parent.command_jsonb <> intent OR parent.authorization_version < 1
       OR length(trim(parent.reason)) NOT BETWEEN 1 AND 500 OR trim(parent.reason) <> parent.reason
       OR parent.request_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(intent),'UTF8')),'hex')
       OR parent.plan_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(parent.plan_jsonb),'UTF8')),'hex')
       OR parent.plan_jsonb->'intent' IS DISTINCT FROM intent
       OR parent.plan_jsonb->'authorization_version' IS DISTINCT FROM to_jsonb(parent.authorization_version)
       OR jsonb_typeof(parent.plan_jsonb->'children') IS DISTINCT FROM 'array'
       OR NOT EXISTS (SELECT 1 FROM public.users actor JOIN public.oam_work_orders wo ON wo.id = parent.oam_work_order_id
            WHERE actor.id = parent.actor_user_id AND actor.person_id = parent.operator_person_id
              AND wo.engineer_person_id = parent.operator_person_id AND wo.status = 'active') THEN
        RAISE EXCEPTION '0098 reversal intent or authorization binding invalid' USING ERRCODE = '23514';
    END IF;
    IF parent.original_replacement_id IS NOT NULL THEN
        SELECT * INTO pair FROM public.work_order_replacements WHERE id = parent.original_replacement_id;
        IF NOT FOUND OR pair.oam_work_order_id <> parent.oam_work_order_id OR pair.operator_person_id <> parent.operator_person_id THEN
            RAISE EXCEPTION '0098 original replacement scope mismatch' USING ERRCODE = '23514'; END IF;
        expected_originals := ARRAY[pair.recover_operation_id,pair.consume_operation_id];
        PERFORM public.rsc_check_work_order_replacement_0093(pair.id);
    ELSE expected_originals := ARRAY[parent.original_operation_id]; END IF;
    IF (SELECT count(*) FROM public.work_order_reversal_items WHERE reversal_id = parent.id) <> cardinality(expected_originals)
       OR jsonb_array_length(parent.plan_jsonb->'children') <> cardinality(expected_originals) THEN
        RAISE EXCEPTION '0098 whole original reversal required' USING ERRCODE = '23514'; END IF;
    FOR item IN SELECT * FROM public.work_order_reversal_items WHERE reversal_id = parent.id ORDER BY ordinal LOOP
        idx := idx + 1;
        SELECT * INTO original FROM public.work_order_material_operations WHERE id = item.original_operation_id;
        SELECT * INTO inverse FROM public.work_order_material_operations WHERE id = item.inverse_operation_id;
        SELECT * INTO original_tx FROM public.inventory_transactions WHERE id = original.posting_transaction_id;
        SELECT * INTO inverse_tx FROM public.inventory_transactions WHERE id = inverse.posting_transaction_id;
        digest := encode(sha256(convert_to('work-order-reversal:' || parent.idempotency_key_hash || ':' || original.id::text,'UTF8')),'hex');
        IF item.ordinal <> idx OR item.original_operation_id <> expected_originals[idx]
           OR original.id IS NULL OR inverse.id IS NULL OR original_tx.id IS NULL OR inverse_tx.id IS NULL
           OR original.oam_work_order_id <> parent.oam_work_order_id OR inverse.oam_work_order_id <> parent.oam_work_order_id
           OR original.operator_person_id <> parent.operator_person_id OR inverse.operator_person_id <> parent.operator_person_id
           OR original.status <> 'posted' OR inverse.status <> 'posted' OR inverse.operation_type <> 'reverse'
           OR original.operation_type NOT IN ('occupy','release','consume','recover') OR inverse.replacement_id IS NOT NULL
           OR (pair.id IS NULL AND original.replacement_id IS NOT NULL)
           OR inverse.idempotency_key_hash <> digest OR inverse_tx.idempotency_key_hash <> digest
           OR inverse.operation_no <> 'WOM-' || upper(substr(replace(parent.oam_work_order_id::text,'-',''),1,12)) || '-' || upper(substr(digest,1,12))
           OR inverse_tx.transaction_no <> 'INV-WO-REVERSE-' || upper(substr(digest,1,20))
           OR original_tx.reversed_transaction_id IS NOT NULL OR inverse_tx.reversed_transaction_id IS DISTINCT FROM original_tx.id
           OR original_tx.actor_user_id <> parent.actor_user_id OR inverse_tx.actor_user_id <> parent.actor_user_id
           OR original_tx.status <> 'posted' OR inverse_tx.status <> 'posted' OR inverse_tx.movement_type <> 'reversal'
           OR original_tx.source_document_type <> 'work_order_material' OR inverse_tx.source_document_type <> 'work_order_material'
           OR original_tx.source_document_id <> parent.oam_work_order_id::text OR inverse_tx.source_document_id <> parent.oam_work_order_id::text
           OR inverse_tx.posting_key <> 'work-order-material:reverse:' || parent.oam_work_order_id::text || ':' || digest
           OR inverse_tx.ledger_cursor IS DISTINCT FROM (parent.plan_jsonb->>'ledger_cursor')::bigint + idx
           OR parent.created_at > inverse_tx.created_at OR inverse_tx.ledger_cursor <= original_tx.ledger_cursor
           OR parent.plan_jsonb->'children'->(idx-1)->>'original_operation_id' IS DISTINCT FROM original.id::text
           OR parent.plan_jsonb->'children'->(idx-1)->>'original_transaction_id' IS DISTINCT FROM original_tx.id::text THEN
            RAISE EXCEPTION '0098 original and inverse operation coordinates invalid' USING ERRCODE = '23514'; END IF;
        PERFORM public.rsc_check_work_order_material_transaction_0090(original_tx.id);
        IF NOT EXISTS (SELECT 1 FROM public.inventory_movements WHERE transaction_id = inverse_tx.id)
           OR (SELECT count(*) FROM public.inventory_movements WHERE transaction_id = original_tx.id)
               <> (SELECT count(*) FROM public.inventory_movements WHERE transaction_id = inverse_tx.id)
           OR (SELECT count(*) FROM public.work_order_material_lines WHERE operation_id = inverse.id)
               <> (SELECT count(*) FROM public.inventory_movements WHERE transaction_id = inverse_tx.id)
           OR EXISTS (SELECT 1 FROM public.inventory_movements old
              LEFT JOIN public.inventory_movements new ON new.transaction_id = inverse_tx.id AND new.line_no = old.line_no
              LEFT JOIN public.work_order_material_lines line ON line.operation_id = inverse.id AND line.line_no = old.line_no
              LEFT JOIN public.stock_accounts account ON account.id = COALESCE(new.from_account_id,new.to_account_id)
              WHERE old.transaction_id = original_tx.id AND (new.id IS NULL OR line.id IS NULL
                OR new.from_account_id IS DISTINCT FROM old.to_account_id OR new.to_account_id IS DISTINCT FROM old.from_account_id
                OR new.quantity <> old.quantity OR new.external_boundary_code IS DISTINCT FROM old.external_boundary_code
                OR line.stock_account_id IS DISTINCT FROM account.id OR line.material_id IS DISTINCT FROM account.material_id
                OR line.condition_before IS DISTINCT FROM account.condition_code OR line.condition_after IS NOT NULL
                OR line.quantity <> new.quantity OR account.custodian_person_id IS DISTINCT FROM parent.operator_person_id
                OR EXISTS (SELECT serial_id FROM public.inventory_movement_serials WHERE movement_id = old.id
                           EXCEPT SELECT serial_id FROM public.inventory_movement_serials WHERE movement_id = new.id)
                OR EXISTS (SELECT serial_id FROM public.inventory_movement_serials WHERE movement_id = new.id
                           EXCEPT SELECT serial_id FROM public.inventory_movement_serials WHERE movement_id = old.id)
                OR EXISTS (SELECT serial_id FROM public.work_order_material_serials WHERE operation_line_id = line.id
                           EXCEPT SELECT serial_id FROM public.inventory_movement_serials WHERE movement_id = old.id)
                OR EXISTS (SELECT serial_id FROM public.inventory_movement_serials WHERE movement_id = old.id
                           EXCEPT SELECT serial_id FROM public.work_order_material_serials WHERE operation_line_id = line.id)
                OR EXISTS (SELECT 1 FROM public.work_order_material_serials WHERE operation_line_id = line.id AND (NOT sku_verified OR NOT qr_verified)))) THEN
            RAISE EXCEPTION '0098 inverse lines or serial set differ from the whole original' USING ERRCODE = '23514'; END IF;
        SELECT jsonb_agg(jsonb_build_object('line_no', line_no, 'from_account_id', from_account_id::text,
            'to_account_id', to_account_id::text, 'quantity', to_char(quantity,'FM999999999999990.000'),
            'external_boundary_code', external_boundary_code, 'serial_ids', COALESCE((SELECT jsonb_agg(sn.serial_id::text ORDER BY sn.serial_id::text)
              FROM public.inventory_movement_serials sn WHERE sn.movement_id = movement.id),'[]'::jsonb)) ORDER BY line_no)
            INTO movements FROM public.inventory_movements movement WHERE transaction_id = inverse_tx.id;
        IF jsonb_typeof(parent.plan_jsonb->'children'->(idx-1)->'movements') IS DISTINCT FROM 'array'
           OR jsonb_array_length(parent.plan_jsonb->'children'->(idx-1)->'movements') <> jsonb_array_length(movements)
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(parent.plan_jsonb->'children'->(idx-1)->'movements') WITH ORDINALITY planned(value,number)
            JOIN public.inventory_movements old ON old.transaction_id = original_tx.id AND old.line_no = planned.number
            JOIN public.stock_accounts account ON account.id = COALESCE(old.to_account_id,old.from_account_id)
            WHERE planned.value->>'original_movement_id' IS DISTINCT FROM old.id::text
               OR planned.value->'line_no' IS DISTINCT FROM to_jsonb(old.line_no)
               OR planned.value->'from_account_id' IS DISTINCT FROM COALESCE(to_jsonb(old.to_account_id::text),'null'::jsonb)
               OR planned.value->'to_account_id' IS DISTINCT FROM COALESCE(to_jsonb(old.from_account_id::text),'null'::jsonb)
               OR planned.value->>'quantity' IS DISTINCT FROM to_char(old.quantity,'FM999999999999990.000')
               OR planned.value->>'material_id' IS DISTINCT FROM account.material_id::text
               OR planned.value->>'condition_code' IS DISTINCT FROM account.condition_code
               OR planned.value->'lot_id' IS DISTINCT FROM COALESCE(to_jsonb(account.lot_id::text),'null'::jsonb)
               OR planned.value->>'reservation_delta' IS DISTINCT FROM to_char(CASE original.operation_type
                    WHEN 'occupy' THEN -old.quantity WHEN 'consume' THEN old.quantity WHEN 'release' THEN old.quantity ELSE 0 END,'FM999999999999990.000')
               OR (SELECT COALESCE(jsonb_agg(value->>'serial_id' ORDER BY value->>'serial_id'),'[]'::jsonb)
                    FROM jsonb_array_elements(planned.value->'serials')) IS DISTINCT FROM movements->(planned.number::integer-1)->'serial_ids') THEN
            RAISE EXCEPTION '0098 persisted plan differs from exact original lines' USING ERRCODE = '23514'; END IF;
        SELECT jsonb_agg((value-'line_no') || jsonb_build_object('quantity',trim_scale((value->>'quantity')::numeric)::text) ORDER BY number)
            INTO stock_movements FROM jsonb_array_elements(movements) WITH ORDINALITY entry(value,number);
        inventory_command := jsonb_build_object('operation','post', 'actor',jsonb_build_object(
            'authorization_version',parent.authorization_version,'person_id',parent.operator_person_id::text,'user_id',parent.actor_user_id),
            'command',jsonb_build_object('effective_at',to_char(inverse_tx.effective_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                'movement_type','reversal','movements',stock_movements,'posting_key',inverse_tx.posting_key,
                'source_document_id',inverse_tx.source_document_id,'source_document_type','work_order_material','transaction_no',inverse_tx.transaction_no));
        IF inverse_tx.request_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(inventory_command),'UTF8')),'hex') THEN
            RAISE EXCEPTION '0098 inverse inventory request hash mismatch' USING ERRCODE = '23514'; END IF;
        command := jsonb_build_object('operation_type','reverse','work_order_id',parent.oam_work_order_id::text,
            'operator_person_id',parent.operator_person_id::text,'reversal_id',parent.id::text,
            'original_operation_id',original.id::text,'original_transaction_id',original_tx.id::text,
            'plan_hash',parent.plan_hash,'movements',movements);
        IF inverse.request_hash <> encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(command),'UTF8')),'hex')
           OR (SELECT count(*) FROM public.audit_events WHERE stream_key = 'material_request'
                AND aggregate_type = 'work_order_material_operation' AND aggregate_id = inverse.id::text
                AND action = 'work_order_material.reverse' AND actor_user_id = parent.actor_user_id
                AND request_id = 'work-order-material:' || digest AND before_jsonb = '{}'::jsonb
                AND after_jsonb = jsonb_build_object('work_order_id',parent.oam_work_order_id::text,'operation_type','reverse',
                    'posting_transaction_id',inverse_tx.id::text,'line_count',jsonb_array_length(movements),
                    'request_hash',inverse.request_hash,'command',command)) <> 1
           OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type = 'work_order_material_operation'
                AND aggregate_id = inverse.id::text AND event_type = 'work_order_material_operation_posted'
                AND idempotency_key = 'work-order-material-operation:' || inverse.id::text
                AND payload_jsonb = jsonb_build_object('work_order_id',parent.oam_work_order_id::text,'operation_type','reverse',
                    'operation_no',inverse.operation_no,'posting_transaction_id',inverse_tx.id::text)) <> 1 THEN
            RAISE EXCEPTION '0098 inverse operation audit or outbox mismatch' USING ERRCODE = '23514'; END IF;
        IF (SELECT count(*) FROM public.audit_events WHERE stream_key = 'inventory' AND aggregate_type = 'inventory_transaction'
            AND aggregate_id = inverse_tx.id::text AND actor_user_id = parent.actor_user_id AND action = 'inventory.transaction.reversed'
            AND request_id = 'inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(parent.request_id,'UTF8')),'hex')
            AND (before_jsonb IS NULL OR before_jsonb = 'null'::jsonb) AND after_jsonb = jsonb_build_object('ledger_cursor',inverse_tx.ledger_cursor,
                'movement_count',jsonb_array_length(movements),'movement_type','reversal','posting_key',inverse_tx.posting_key,
                'reversed_transaction_id',original_tx.id::text,'status','posted')) <> 1
           OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type = 'inventory_transaction'
            AND aggregate_id = inverse_tx.id::text AND event_type = 'inventory.transaction.reversed'
            AND payload_jsonb = jsonb_build_object('transaction_id',inverse_tx.id::text,'transaction_no',inverse_tx.transaction_no,
                'movement_type','reversal','ledger_cursor',inverse_tx.ledger_cursor,'reversed_transaction_id',original_tx.id::text)) <> 1
           OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type = 'inventory_transaction'
            AND aggregate_id = inverse_tx.id::text AND actor_id = parent.actor_user_id AND from_status IS NULL AND to_status = 'posted'
            AND reason = 'inventory_transaction_reversed' AND metadata_jsonb = jsonb_build_object('ledger_cursor',inverse_tx.ledger_cursor,
                'movement_type','reversal','request_reference','inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(parent.request_id,'UTF8')),'hex'))) <> 1 THEN
            RAISE EXCEPTION '0098 inverse inventory audit or state evidence mismatch' USING ERRCODE = '23514'; END IF;
        children := children || jsonb_build_array(jsonb_build_object('ordinal',idx,'original_operation_id',original.id::text,'inverse_operation_id',inverse.id::text));
    END LOOP;
    payload := jsonb_build_object('work_order_id',parent.oam_work_order_id::text,'request_hash',parent.request_hash,
        'plan_hash',parent.plan_hash,'command',intent,'items',children);
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key = 'material_request'
        AND aggregate_type = 'work_order_material_reversal' AND aggregate_id = parent.id::text
        AND action = 'work_order_material.reversal' AND actor_user_id = parent.actor_user_id AND request_id = parent.request_id
        AND before_jsonb = '{}'::jsonb AND after_jsonb = payload) <> 1
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type = 'work_order_material_reversal'
        AND aggregate_id = parent.id::text AND event_type = 'work_order_material_reversal_posted'
        AND idempotency_key = 'work-order-material-reversal:' || parent.id::text AND payload_jsonb = payload) <> 1 THEN
        RAISE EXCEPTION '0098 parent compensation audit or outbox mismatch' USING ERRCODE = '23514'; END IF;
END;
"""

DISPATCH_BODY = """
BEGIN
    IF TG_TABLE_NAME = 'work_order_reversals' THEN
        PERFORM public.rsc_check_work_order_reversal_0098(NEW.id);
    ELSE
        PERFORM public.rsc_check_work_order_reversal_0098(NEW.reversal_id);
    END IF;
    RETURN NULL;
END;
"""

FUNCTIONS = {
    ("rsc_check_work_order_reservations_0098", "text, bigint"): ("checked_order text, checked_cursor bigint", "void", RESERVATION_BODY),
    ("rsc_check_work_order_reversal_0098", "uuid"): ("checked_reversal uuid", "void", CHECK_BODY),
    ("rsc_dispatch_work_order_reversal_0098", ""): ("", "trigger", DISPATCH_BODY),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}
TRIGGERS = {}
for _table in TABLES:
    TRIGGERS[f"trg_{_table}_proof_0098"] = (_table, "INSERT", "rsc_dispatch_work_order_reversal_0098", 5, True)
    TRIGGERS[f"trg_{_table}_immutable_0098"] = (_table, "UPDATE OR DELETE", "rsc_guard_work_order_facts_0090", 27, False)
    TRIGGERS[f"trg_{_table}_no_truncate_0098"] = (_table, "TRUNCATE", "rsc_guard_work_order_facts_0090", 34, False)


def _create_tables():
    uid = sa.Uuid()
    op.create_table(TABLES[0],
        sa.Column("id",uid,primary_key=True), sa.Column("reversal_no",sa.String(100),nullable=False),
        sa.Column("oam_work_order_id",uid,sa.ForeignKey("oam_work_orders.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("actor_user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("operator_person_id",uid,sa.ForeignKey("people.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("authorization_version",sa.BigInteger(),nullable=False),
        sa.Column("original_operation_id",uid,sa.ForeignKey("work_order_material_operations.id")),
        sa.Column("original_replacement_id",uid,sa.ForeignKey("work_order_replacements.id")),
        sa.Column("reason",sa.Text(),nullable=False), sa.Column("request_id",sa.String(160),nullable=False),
        *(sa.Column(name,sa.String(64),nullable=False) for name in ("idempotency_key_hash","request_hash","plan_hash")),
        *(sa.Column(name,sa.JSON().with_variant(postgresql.JSONB(),"postgresql"),nullable=False) for name in ("command_jsonb","plan_jsonb")),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("reversal_no",name="uq_work_order_reversals_no"),
        sa.UniqueConstraint("idempotency_key_hash",name="uq_work_order_reversals_key"),
        sa.UniqueConstraint("actor_user_id","request_id",name="uq_work_order_reversals_request"),
        sa.UniqueConstraint("original_operation_id",name="uq_work_order_reversals_original_operation"),
        sa.UniqueConstraint("original_replacement_id",name="uq_work_order_reversals_original_pair"),
        sa.CheckConstraint("(original_operation_id IS NULL) <> (original_replacement_id IS NULL)",name="ck_work_order_reversals_one_original"),
        sa.CheckConstraint("authorization_version > 0 AND length(reason) BETWEEN 1 AND 500",name="ck_work_order_reversals_context"))
    op.create_table(TABLES[1], sa.Column("id",uid,primary_key=True),
        sa.Column("reversal_id",uid,sa.ForeignKey("work_order_reversals.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("ordinal",sa.BigInteger(),nullable=False),
        sa.Column("original_operation_id",uid,sa.ForeignKey("work_order_material_operations.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("inverse_operation_id",uid,sa.ForeignKey("work_order_material_operations.id",deferrable=True,initially="DEFERRED"),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("reversal_id","ordinal",name="uq_work_order_reversal_items_order"),
        sa.UniqueConstraint("original_operation_id",name="uq_work_order_reversal_items_original"),
        sa.UniqueConstraint("inverse_operation_id",name="uq_work_order_reversal_items_inverse"),
        sa.CheckConstraint("ordinal IN (1,2) AND original_operation_id <> inverse_operation_id",name="ck_work_order_reversal_items_distinct"))
    if op.get_bind().dialect.name == "sqlite":
        for table in TABLES:
            for event in ("UPDATE","DELETE"):
                op.execute(f"CREATE TRIGGER trg_{table}_{event.lower()}_0098 BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'0098 reversals are append-only'); END")


def _sources():
    folder = Path(__file__).parent
    previous = runpy.run_path(str(folder / "20261007_0097_work_order_reversal_boundary.py"))
    old = previous["sources"]()[1]
    guard = """    IF EXISTS (SELECT 1 FROM public.inventory_transactions inv
        JOIN public.inventory_transactions orig ON orig.id = inv.reversed_transaction_id
        WHERE (inv.id = tx.id OR orig.id = tx.id)
          AND (orig.source_document_type = 'work_order_material' OR inv.source_document_type = 'work_order_material'
            OR EXISTS (SELECT 1 FROM public.work_order_material_operations WHERE posting_transaction_id = orig.id))
          AND NOT EXISTS (SELECT 1 FROM public.work_order_reversal_items item
            JOIN public.work_order_material_operations before ON before.id = item.original_operation_id
            JOIN public.work_order_material_operations after ON after.id = item.inverse_operation_id
            WHERE before.posting_transaction_id = orig.id AND after.posting_transaction_id = inv.id)) THEN
        RAISE EXCEPTION '0097 work order reversal requires dedicated compensation proof' USING ERRCODE = '23514';
    END IF;
    IF tx.reversed_transaction_id IS NOT NULL AND tx.source_document_type = 'work_order_material' THEN
        PERFORM public.rsc_check_work_order_reversal_0098(item.reversal_id)
          FROM public.work_order_reversal_items item JOIN public.work_order_material_operations inverse_fact ON inverse_fact.id = item.inverse_operation_id
          WHERE inverse_fact.posting_transaction_id = tx.id;
        IF NOT FOUND THEN RAISE EXCEPTION '0098 inverse command missing' USING ERRCODE = '23514'; END IF;
        PERFORM public.rsc_check_work_order_reservations_0098(tx.source_document_id,tx.ledger_cursor);
        RETURN;
    END IF;
"""
    if old.count(previous["GUARD"]) != 1:
        raise RuntimeError("0098 original reversal guard source drift")
    new = old.replace(previous["GUARD"],guard)
    anchor = "    -- Use ledger cursor order, never caller-controlled effective time or account balance."
    if new.count(anchor) != 1:
        raise RuntimeError("0098 original reservation source drift")
    new = new[:new.index(anchor)] + "    PERFORM public.rsc_check_work_order_reservations_0098(tx.source_document_id,tx.ledger_cursor);\nEND;\n"
    result = {"public.rsc_check_work_order_material_transaction_0090(uuid)": (old,new)}
    serial_old = runpy.run_path(str(folder / "20261003_0093_work_order_replacements.py"))["_sources"]()["public.rsc_check_serial_lifecycle_0092(uuid)"][1]
    serial_new = serial_old.replace("move record;", "move record;\n    original_move public.inventory_movements%ROWTYPE;\n    snapshots jsonb := '{}'::jsonb;\n    prior jsonb;")
    serial_new = serial_new.replace("tx.ledger_cursor, tx.movement_type, tx.source_document_type,", "tx.ledger_cursor, tx.movement_type, tx.source_document_type, tx.reversed_transaction_id,")
    branch = """        IF move.movement_type = 'reversal' AND move.source_document_type = 'work_order_material' THEN
            SELECT * INTO original_move FROM public.inventory_movements WHERE id = expected_movement;
            prior := snapshots->expected_movement::text;
            IF NOT FOUND OR prior IS NULL OR original_move.transaction_id IS DISTINCT FROM move.reversed_transaction_id
               OR move.ledger_cursor <= expected_cursor OR move.from_account_id IS DISTINCT FROM expected_account
               OR move.from_account_id IS DISTINCT FROM original_move.to_account_id
               OR move.to_account_id IS DISTINCT FROM original_move.from_account_id
               OR move.quantity <> original_move.quantity OR move.external_boundary_code IS DISTINCT FROM original_move.external_boundary_code
               OR (move.from_account_id IS NOT NULL AND (move.source_material IS DISTINCT FROM serial.material_id OR move.source_lot IS DISTINCT FROM serial.lot_id))
               OR (move.to_account_id IS NOT NULL AND (move.target_material IS DISTINCT FROM serial.material_id OR move.target_lot IS DISTINCT FROM serial.lot_id))
               OR move.quantity <> (SELECT count(*) FROM public.inventory_movement_serials WHERE movement_id = move.id)
               OR (SELECT count(*) FROM public.material_inventory_policies policy WHERE policy.material_id = serial.material_id
                    AND policy.effective_from <= move.effective_at AND (policy.effective_to IS NULL OR policy.effective_to > move.effective_at)
                    AND policy.tracking_mode IN ('serial','lot_and_serial')) <> 1
               OR NOT EXISTS (SELECT 1 FROM public.work_order_reversal_items item
                    JOIN public.work_order_material_operations original ON original.id = item.original_operation_id
                    JOIN public.work_order_material_operations inverse ON inverse.id = item.inverse_operation_id
                    JOIN public.work_order_reversals parent ON parent.id = item.reversal_id
                    WHERE original.posting_transaction_id = move.reversed_transaction_id AND inverse.posting_transaction_id = move.transaction_id
                      AND inverse.operation_type = 'reverse' AND original.operation_type <> 'reverse'
                      AND original.oam_work_order_id = parent.oam_work_order_id AND inverse.oam_work_order_id = parent.oam_work_order_id)
               OR (prior->>'account')::uuid IS DISTINCT FROM move.to_account_id THEN
                RAISE EXCEPTION '0098 serial inverse requires the last exact original movement' USING ERRCODE = '23514'; END IF;
            IF expected_status IS DISTINCT FROM prior->>'status' THEN terminal_time := move.created_at; END IF;
            expected_status := prior->>'status';
            expected_owner := (prior->>'owner')::uuid;
            expected_account := move.to_account_id;
            expected_movement := move.id;
            expected_cursor := move.ledger_cursor;
            CONTINUE;
        END IF;
        snapshots := snapshots || jsonb_build_object(move.id::text,jsonb_build_object(
            'status',expected_status,'owner',expected_owner::text,'account',expected_account::text));
"""
    serial_new = serial_new.replace("    LOOP\n", "    LOOP\n" + branch)
    result["public.rsc_check_serial_lifecycle_0092(uuid)"] = (serial_old,serial_new)
    origin_old = runpy.run_path(str(folder / "20261006_0096_removed_serial_registrations.py"))["ORIGIN_BODY"]
    origin_new = origin_old.replace("WHERE serial.serial_id = NEW.serial_id ORDER BY", """WHERE serial.serial_id = NEW.serial_id AND movement.from_account_id IS NULL
          AND movement.to_account_id IS NOT NULL AND tx.movement_type <> 'reversal'
          AND NOT EXISTS (SELECT 1 FROM public.inventory_transactions inverse WHERE inverse.reversed_transaction_id = tx.id)
        ORDER BY""")
    origin_new = origin_new.replace("IF NOT FOUND THEN RAISE EXCEPTION '0096 registered serial movement lacks posting' USING ERRCODE = '23514'; END IF;", """IF NOT FOUND THEN
        IF EXISTS (SELECT 1 FROM public.inventory_movement_serials sn
            JOIN public.inventory_transactions tx ON tx.id = sn.transaction_id
            JOIN public.work_order_material_operations operation ON operation.posting_transaction_id = tx.id
            JOIN public.work_order_reversal_items item ON item.inverse_operation_id = operation.id
            WHERE sn.serial_id = NEW.serial_id AND tx.movement_type = 'reversal' AND tx.status = 'posted') THEN RETURN NULL; END IF;
        RAISE EXCEPTION '0096 registered serial movement lacks posting' USING ERRCODE = '23514';
    END IF;""")
    result["public.rsc_check_removed_origin_0096()"] = (origin_old,origin_new)
    return result


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql","sqlite"}: raise RuntimeError("0098 supports PostgreSQL and SQLite only")
    folder = Path(__file__).parent
    helper = runpy.run_path(str(folder / "20260927_0087_inbound_fulfillment_boundary.py"))
    helper["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.inventory_transactions, public.inventory_movements, public.inventory_movement_serials, public.inventory_serials, public.serial_current_positions, public.work_order_material_operations, public.work_order_material_lines, public.work_order_material_serials, public.work_order_replacements IN SHARE ROW EXCLUSIVE MODE")
    if upgrade:
        prior = runpy.run_path(str(folder / "20261007_0097_work_order_reversal_boundary.py"))
        helper["_preflight"]("EXISTS (" + prior["forbidden_reversals"]() + ")",
            "0098 upgrade blocked: detached reversal history requires reviewed compensation")
    if not upgrade:
        if db.dialect.name == "postgresql": op.execute("LOCK TABLE public.work_order_reversals, public.work_order_reversal_items IN SHARE ROW EXCLUSIVE MODE")
        helper["_preflight"](" OR ".join(f"EXISTS (SELECT 1 FROM {table})" for table in TABLES),
            "0098 downgrade blocked: immutable reversal history must be retained")
    if upgrade: _create_tables()
    if db.dialect.name == "postgresql":
        replacement = runpy.run_path(str(folder / "20260912_0072_outbound_postings.py"))["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
        if upgrade:
            for (name,signature),(args,result,body) in FUNCTIONS.items():
                op.execute(f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
                op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
            for name,(table,events,function,_,deferred) in TRIGGERS.items():
                sql = (f"CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW" if deferred else
                    f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events == 'TRUNCATE' else 'ROW'}")
                op.execute(sql + f" EXECUTE FUNCTION public.{function}()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
            op.execute("GRANT SELECT, INSERT ON public.work_order_reversals, public.work_order_reversal_items TO star_oam_api")
        else:
            for (name,signature),digest in FUNCTION_HASHES.items():
                op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                    WHERE p.oid='public.{name}({signature})'::regprocedure
                      AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{digest}'
                      AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND p.prosecdef
                      AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                      AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE acl.grantee<>p.proowner))
                    THEN RAISE EXCEPTION '0098 function source, ownership or ACL drift'; END IF; END $body$""")
        for signature,(old,new) in _sources().items():
            replacement(signature=signature, expected_hash=hashlib.sha256((old if upgrade else new).encode()).hexdigest(),
                replacement_hash=hashlib.sha256((new if upgrade else old).encode()).hexdigest(),
                replacements=((old,new),) if upgrade else ((new,old),),label="work_order_reversal_0098")
        replacement(signature="public.rsc_oam_runtime_binding_ready_0044()",expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),
            label="work_order_reversal_readiness_0098")
        if not upgrade:
            for name,(table,*_) in TRIGGERS.items(): op.execute(f"DROP TRIGGER {name} ON public.{table}")
            for name,signature in reversed(FUNCTIONS): op.execute(f"DROP FUNCTION public.{name}({signature})")
    if not upgrade:
        for table in reversed(TABLES): op.drop_table(table)


def upgrade(): _transition(True)
def downgrade(): _transition(False)
