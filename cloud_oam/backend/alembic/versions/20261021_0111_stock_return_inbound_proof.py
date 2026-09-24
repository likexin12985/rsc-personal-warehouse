"""Prove the independent inbound graph before admitting return transit moves.

No historical fact is rewritten or inferred. Databases with pre-0111 inbounds
require investigation before this migration; a populated downgrade is blocked.
SQLite proves retention/ordering only. Deferred integrity requires the PG16 gate.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = "20261021_0111"
down_revision = "20261020_0110"
branch_labels = depends_on = None
OLD_HASH = "2e5527dd8ffbfc5c799fa215bcdb5de1d078d62ec7b6ce6f7d621ef40034b935"
NEW_HASH = "13c5383d20ecc1bbb4a503a19dbd92f52259a61eea7a19d6c7fd3747f8109e13"

CHECK_BODY = r"""
DECLARE
    fact public.stock_operation_return_inbounds%ROWTYPE;
    receipt public.stock_operation_receipts%ROWTYPE;
    tx public.inventory_transactions%ROWTYPE;
    detail record;
    ordinal integer := 0;
    serials jsonb;
    planned jsonb := '[]'::jsonb;
    commands jsonb := '[]'::jsonb;
    hashed_commands jsonb := '[]'::jsonb;
    plan jsonb;
    command jsonb;
    body jsonb;
    request_body jsonb;
    reference text;
    stored_time text;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0111 inbound proof requires read committed' USING ERRCODE='23514';
    END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0111 inventory head missing' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_stock_return_inbound_0106(checked_inbound);
    SELECT * INTO fact FROM public.stock_operation_return_inbounds WHERE id=checked_inbound;
    SELECT * INTO receipt FROM public.stock_operation_receipts WHERE id=fact.receipt_id;
    SELECT * INTO tx FROM public.inventory_transactions WHERE id=fact.posting_transaction_id;
    IF fact.actor_user_id<>receipt.actor_user_id OR fact.operator_person_id<>receipt.operator_person_id
       OR fact.receipt_plan_hash<>receipt.plan_hash OR fact.reason<>receipt.reason
       OR fact.inbound_no<>'RET-IN-' || upper(substr(replace(fact.id::text,'-',''),1,20))
       OR fact.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
       OR fact.request_hash !~ '^[a-f0-9]{64}$' OR fact.plan_hash !~ '^[a-f0-9]{64}$'
       OR fact.idempotency_key_hash !~ '^[a-f0-9]{64}$'
       OR tx.idempotency_key_hash<>fact.idempotency_key_hash
       OR fact.audit_version<=receipt.audit_version
       OR NOT (receipt.created_at<=tx.effective_at AND tx.effective_at<=fact.created_at
           AND fact.created_at<=tx.created_at AND tx.created_at=tx.posted_at AND tx.posted_at<=clock_timestamp())
       OR tx.reversed_transaction_id IS NOT NULL
       OR EXISTS (SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id=tx.id)
       OR (SELECT count(*) FROM public.inventory_transactions WHERE
            (source_document_type='stock_return_receipt_inbound' AND source_document_id=fact.id::text)
            OR posting_key=tx.posting_key OR idempotency_key_hash=fact.idempotency_key_hash)<>1
       OR NOT EXISTS (SELECT 1 FROM public.stock_locations location
            JOIN public.custody_assignments custody ON custody.location_id=location.id
            JOIN public.stock_operation_shipments parcel ON parcel.id=fact.shipment_id
            JOIN public.stock_operation_orders parent ON parent.id=parcel.operation_id
            WHERE location.id=fact.target_location_id AND location.location_type='region'
              AND custody.id=fact.target_custody_assignment_id AND custody.custodian_person_id=fact.operator_person_id
              AND parent.target_location_id=location.id AND parcel.target_custody_assignment_id=custody.id
              AND custody.valid_from<=tx.effective_at AND (custody.valid_to IS NULL OR custody.valid_to>fact.created_at)) THEN
        RAISE EXCEPTION '0111 inbound coordinates, chronology or posting mismatch' USING ERRCODE='23514';
    END IF;
    FOR detail IN
        SELECT line.*, accepted.shipment_line_id, accepted.accepted_qty AS expected_qty,
            departure.transit_stock_account_id AS transit_id,
            source.owner_org_id AS source_owner, target.owner_org_id AS target_owner,
            target.location_id AS target_location, target.custodian_person_id AS target_person,
            location.owner_org_id AS location_owner,
            (SELECT count(*) FROM public.material_inventory_policies p WHERE p.material_id=line.material_id
                AND p.effective_from<=tx.effective_at AND (p.effective_to IS NULL OR p.effective_to>tx.effective_at)) AS policies,
            policy.tracking_mode, policy.quantity_scale, policy.allow_fraction
        FROM public.stock_operation_return_inbound_lines line
        JOIN public.stock_operation_receipt_lines accepted ON accepted.id=line.receipt_line_id AND accepted.receipt_id=fact.receipt_id
        JOIN public.stock_operation_shipment_lines parcel ON parcel.id=accepted.shipment_line_id
        JOIN public.stock_operation_outbound_lines departure ON departure.id=parcel.outbound_line_id
        JOIN public.stock_accounts source ON source.id=line.source_account_id
        JOIN public.stock_accounts target ON target.id=line.target_account_id
        JOIN public.stock_locations location ON location.id=fact.target_location_id
        LEFT JOIN public.material_inventory_policies policy ON policy.material_id=line.material_id
            AND policy.effective_from<=tx.effective_at AND (policy.effective_to IS NULL OR policy.effective_to>tx.effective_at)
        WHERE line.inbound_id=fact.id ORDER BY line.line_no
    LOOP
        ordinal:=ordinal+1;
        IF detail.line_no<>ordinal OR ordinal>100 OR detail.accepted_qty<>detail.expected_qty
           OR detail.source_account_id<>detail.transit_id OR detail.source_owner<>detail.target_owner
           OR detail.source_owner<>detail.location_owner OR detail.target_location<>fact.target_location_id
           OR detail.target_person IS DISTINCT FROM fact.operator_person_id OR detail.created_at<>fact.created_at
           OR detail.policies<>1 OR round(detail.accepted_qty,detail.quantity_scale::integer)<>detail.accepted_qty
           OR (NOT detail.allow_fraction AND trunc(detail.accepted_qty)<>detail.accepted_qty)
           OR (detail.tracking_mode IN ('lot','lot_and_serial') AND detail.lot_id IS NULL)
           OR detail.receipt_line_id IS DISTINCT FROM (SELECT id FROM public.stock_operation_receipt_lines
                WHERE receipt_id=fact.receipt_id AND accepted_qty>0 ORDER BY line_no OFFSET ordinal-1 LIMIT 1) THEN
            RAISE EXCEPTION '0111 exact accepted line, account or policy required' USING ERRCODE='23514';
        END IF;
        SELECT coalesce(jsonb_agg(serial_id::text ORDER BY serial_id),'[]'::jsonb) INTO serials
            FROM public.stock_operation_receipt_serials WHERE line_id=detail.receipt_line_id AND result='accepted';
        IF serials IS DISTINCT FROM (SELECT coalesce(jsonb_agg(serial_id::text ORDER BY serial_id),'[]'::jsonb)
                FROM public.stock_operation_return_inbound_serials WHERE line_id=detail.id)
           OR (detail.tracking_mode IN ('serial','lot_and_serial') AND jsonb_array_length(serials)<>detail.accepted_qty)
           OR (detail.tracking_mode NOT IN ('serial','lot_and_serial') AND serials<>'[]'::jsonb)
           OR jsonb_array_length(serials)>1000
           OR EXISTS (SELECT 1 FROM public.stock_operation_return_inbound_serials sn
                LEFT JOIN public.stock_operation_receipt_serials accepted ON accepted.id=sn.receipt_serial_id
                LEFT JOIN public.inventory_serials physical ON physical.id=sn.serial_id
                WHERE sn.line_id=detail.id AND (sn.inbound_id<>fact.id OR sn.created_at<>fact.created_at
                    OR accepted.line_id IS DISTINCT FROM detail.receipt_line_id OR accepted.result IS DISTINCT FROM 'accepted'
                    OR accepted.serial_id IS DISTINCT FROM sn.serial_id OR physical.material_id IS DISTINCT FROM detail.material_id
                    OR physical.lot_id IS DISTINCT FROM detail.lot_id
                    OR detail.source_account_id IS DISTINCT FROM (SELECT movement.to_account_id
                        FROM public.inventory_movements movement JOIN public.inventory_transactions previous ON previous.id=movement.transaction_id
                        JOIN public.inventory_movement_serials proof ON proof.movement_id=movement.id
                        WHERE proof.serial_id=sn.serial_id AND previous.status='posted' AND previous.ledger_cursor<tx.ledger_cursor
                        ORDER BY previous.ledger_cursor DESC,movement.line_no DESC LIMIT 1))) THEN
            RAISE EXCEPTION '0111 exact accepted SN and original transit position required' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.inventory_movements movement
                WHERE movement.transaction_id=tx.id AND movement.line_no=ordinal
                  AND movement.from_account_id=detail.source_account_id AND movement.to_account_id=detail.target_account_id
                  AND movement.quantity=detail.accepted_qty AND movement.external_boundary_code IS NULL AND movement.created_at=tx.created_at
                  AND serials=(SELECT coalesce(jsonb_agg(sn.serial_id::text ORDER BY sn.serial_id),'[]'::jsonb)
                      FROM public.inventory_movement_serials sn WHERE sn.movement_id=movement.id)
                  AND NOT EXISTS (SELECT 1 FROM public.inventory_movement_serials sn WHERE sn.movement_id=movement.id
                      AND (sn.transaction_id<>tx.id OR sn.created_at<>tx.created_at))) THEN
            RAISE EXCEPTION '0111 exact inbound inventory movement required' USING ERRCODE='23514';
        END IF;
        planned:=planned || jsonb_build_array(jsonb_build_object('receipt_line_id',detail.receipt_line_id::text,
            'shipment_line_id',detail.shipment_line_id::text,'source_account_id',detail.source_account_id::text,
            'target_account_id',detail.target_account_id::text,'material_id',detail.material_id::text,
            'condition_code',detail.condition_code,'lot_id',detail.lot_id::text,'accepted_qty',detail.accepted_qty::numeric(18,3)::text,'serial_ids',serials));
        commands:=commands || jsonb_build_array(jsonb_build_object('from_account_id',detail.source_account_id::text,
            'to_account_id',detail.target_account_id::text,'quantity',detail.accepted_qty::numeric(18,3)::text,'serial_ids',serials));
        hashed_commands:=hashed_commands || jsonb_build_array(jsonb_build_object('external_boundary_code',NULL,
            'from_account_id',detail.source_account_id::text,'to_account_id',detail.target_account_id::text,
            'quantity',trim_scale(detail.accepted_qty)::text,'serial_ids',serials));
    END LOOP;
    IF ordinal=0 OR ordinal<>(SELECT count(*) FROM public.stock_operation_receipt_lines WHERE receipt_id=fact.receipt_id AND accepted_qty>0)
       OR ordinal<>(SELECT count(*) FROM public.stock_operation_return_inbound_lines WHERE inbound_id=fact.id)
       OR ordinal<>(SELECT count(*) FROM public.inventory_movements WHERE transaction_id=tx.id)
       OR NOT EXISTS (SELECT 1 FROM public.stock_operation_return_inbound_postings
            WHERE inbound_id=fact.id AND inventory_transaction_id=tx.id AND created_at=fact.created_at)
       OR EXISTS (SELECT 1 FROM public.stock_operation_return_inbound_lines line WHERE line.inbound_id=fact.id
            GROUP BY line.source_account_id HAVING sum(line.accepted_qty)>
                (SELECT coalesce(sum(CASE WHEN m.to_account_id=line.source_account_id THEN m.quantity ELSE 0 END),0)
                      -coalesce(sum(CASE WHEN m.from_account_id=line.source_account_id THEN m.quantity ELSE 0 END),0)
                 FROM public.inventory_movements m JOIN public.inventory_transactions prior ON prior.id=m.transaction_id
                 WHERE prior.status='posted' AND prior.ledger_cursor<tx.ledger_cursor)) THEN
        RAISE EXCEPTION '0111 incomplete accepted graph or insufficient original transit quantity' USING ERRCODE='23514';
    END IF;
    plan:=jsonb_build_object('schema_version','1.0','receipt_id',fact.receipt_id::text,'shipment_id',fact.shipment_id::text,
        'operator_person_id',fact.operator_person_id::text,'authorization_version',fact.authorization_version,
        'target_location_id',fact.target_location_id::text,'target_custody_assignment_id',fact.target_custody_assignment_id::text,
        'receipt_plan_hash',receipt.plan_hash,'reason',receipt.reason,'ledger_cursor',tx.ledger_cursor-1,'lines',planned);
    stored_time:=to_char(tx.effective_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS') ||
        CASE WHEN extract(microseconds FROM tx.effective_at)::bigint % 1000000=0 THEN ''
             ELSE '.' || to_char(tx.effective_at AT TIME ZONE 'UTC','US') END || '+00:00';
    command:=jsonb_build_object('source_document_type','stock_return_receipt_inbound','source_document_id',fact.id::text,
        'posting_key','stock-return-receipt-inbound:' || fact.receipt_id::text,'movement_type','transfer',
        'effective_at',stored_time,'movements',commands);
    request_body:=jsonb_build_object('operation','post','actor',jsonb_build_object('authorization_version',fact.authorization_version,
        'person_id',fact.operator_person_id::text,'user_id',fact.actor_user_id),'command',jsonb_build_object(
        'effective_at',to_char(tx.effective_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'movement_type','transfer','movements',hashed_commands,'posting_key',tx.posting_key,
        'source_document_id',fact.id::text,'source_document_type','stock_return_receipt_inbound','transaction_no',tx.transaction_no));
    IF fact.plan_jsonb IS DISTINCT FROM plan OR fact.command_jsonb IS DISTINCT FROM command
       OR (receipt.plan_jsonb->>'ledger_cursor')::bigint>tx.ledger_cursor-1
       OR tx.transaction_no<>'INV-RETURN-IN-' || upper(substr(replace(fact.id::text,'-',''),1,16))
       OR fact.plan_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(plan),'UTF8')),'hex')
       OR fact.request_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(jsonb_build_object(
            'receipt_id',fact.receipt_id::text,'request_id',fact.request_id,'plan_hash',fact.plan_hash)),'UTF8')),'hex')
       OR tx.request_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(request_body),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0111 canonical plan, command or request hash mismatch' USING ERRCODE='23514';
    END IF;
    reference:='inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(fact.request_id,'UTF8')),'hex');
    body:=jsonb_build_object('schema_version','1.0','inbound_id',fact.id::text,'inbound_no',fact.inbound_no,
        'receipt_id',fact.receipt_id::text,'shipment_id',fact.shipment_id::text,'target_location_id',fact.target_location_id::text,
        'target_custody_assignment_id',fact.target_custody_assignment_id::text,'status','posted',
        'posting_transaction_id',tx.id::text,'request_id',fact.request_id,'request_hash',fact.request_hash,'plan_hash',fact.plan_hash,'replayed',false);
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='material_request' AND aggregate_type='stock_operation_return_inbound' AND aggregate_id=fact.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='material_request' AND aggregate_type='stock_operation_return_inbound'
            AND aggregate_id=fact.id::text AND actor_user_id=fact.actor_user_id AND stream_version=fact.audit_version
            AND action='stock_return_inbound_posted' AND request_id=fact.request_id AND before_jsonb='{}'::jsonb AND after_jsonb=body
            AND created_at=fact.created_at AND occurred_at=fact.created_at)
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type='stock_operation_return_inbound' AND aggregate_id=fact.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.outbox_events WHERE aggregate_type='stock_operation_return_inbound' AND aggregate_id=fact.id::text
            AND event_type='stock_return_inbound_posted' AND idempotency_key='stock-return-inbound:' || fact.id::text AND payload_jsonb=body AND created_at=fact.created_at)
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type='stock_operation_return_inbound' AND aggregate_id=fact.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type='stock_operation_return_inbound' AND aggregate_id=fact.id::text
            AND from_status IS NULL AND to_status='posted' AND actor_id=fact.actor_user_id AND reason='stock_return_inbound_posted'
            AND idempotency_key='stock-return-inbound:' || fact.id::text AND metadata_jsonb=body AND occurred_at=fact.created_at AND created_at=fact.created_at)
       OR (SELECT count(*) FROM public.audit_events WHERE stream_key='inventory' AND aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='inventory' AND aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text
            AND actor_user_id=fact.actor_user_id AND action='inventory.transaction.posted' AND request_id=reference
            AND (before_jsonb IS NULL OR before_jsonb='null'::jsonb) AND after_jsonb=jsonb_build_object('ledger_cursor',tx.ledger_cursor,
                'movement_count',ordinal,'movement_type','transfer','posting_key',tx.posting_key,'reversed_transaction_id',NULL,'status','posted')
            AND occurred_at=tx.posted_at AND created_at=tx.created_at)
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text
            AND actor_id=fact.actor_user_id AND from_status IS NULL AND to_status='posted' AND reason='inventory_transaction_posted'
            AND idempotency_key='inventory-state-' || encode(sha256(convert_to('cloud_oam.inventory.state.v1','UTF8') || decode('00','hex') || convert_to(tx.id::text,'UTF8') || decode('00','hex') || convert_to('posted','UTF8')),'hex')
            AND metadata_jsonb=jsonb_build_object('ledger_cursor',tx.ledger_cursor,'movement_type','transfer','request_reference',reference)
            AND occurred_at=tx.posted_at AND created_at=tx.created_at)
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.outbox_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text
            AND event_type='inventory.transaction.posted' AND idempotency_key='inventory-outbox-' || encode(sha256(convert_to('cloud_oam.inventory.outbox.v1','UTF8') || decode('00','hex') || convert_to(tx.id::text,'UTF8') || decode('00','hex') || convert_to('posted','UTF8')),'hex')
            AND payload_jsonb=jsonb_build_object('transaction_id',tx.id::text,'transaction_no',tx.transaction_no,'movement_type','transfer','ledger_cursor',tx.ledger_cursor,'reversed_transaction_id',NULL)
            AND created_at=tx.created_at) THEN
        RAISE EXCEPTION '0111 complete inbound and inventory audit, state and outbox required' USING ERRCODE='23514';
    END IF;
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='material_request' AND actor_user_id=fact.actor_user_id AND request_id=fact.request_id)<>1
       OR (SELECT count(*) FROM public.audit_events WHERE stream_key='inventory' AND actor_user_id=fact.actor_user_id AND request_id=reference)<>1
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type='inventory_transaction' AND actor_id=fact.actor_user_id AND metadata_jsonb->>'request_reference'=reference)<>1
       OR EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='material_request' AND actor_user_id=fact.actor_user_id
            AND aggregate_type IN ('stock_operation_command_seal','stock_operation_return_inbound_seal') AND after_jsonb->>'request_id'=fact.request_id) THEN
        RAISE EXCEPTION '0111 inbound request has conflicting execution or seal evidence' USING ERRCODE='23514';
    END IF;
END;
"""

DISPATCH_BODY = r"""
DECLARE identifier uuid; checked_tx uuid; tx public.inventory_transactions%ROWTYPE; fact public.stock_operation_return_inbounds%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='stock_operation_return_inbounds' THEN identifier:=NEW.id;
    ELSIF TG_TABLE_NAME IN ('stock_operation_return_inbound_lines','stock_operation_return_inbound_serials','stock_operation_return_inbound_postings') THEN identifier:=NEW.inbound_id;
    ELSIF TG_TABLE_NAME='inventory_transactions' THEN checked_tx:=NEW.id;
    ELSIF TG_TABLE_NAME IN ('inventory_movements','inventory_movement_serials') THEN checked_tx:=NEW.transaction_id;
    ELSE
        IF NEW.aggregate_type='stock_operation_return_inbound' THEN identifier:=NEW.aggregate_id::uuid;
        ELSIF NEW.aggregate_type='inventory_transaction' THEN checked_tx:=NEW.aggregate_id::uuid;
        END IF;
        -- A late event with a reused request but another aggregate must also
        -- re-prove the original fact, even when no original row is touched.
        IF TG_TABLE_NAME='audit_events' THEN
            FOR fact IN SELECT * FROM public.stock_operation_return_inbounds i WHERE i.actor_user_id=NEW.actor_user_id
                AND ((NEW.stream_key='material_request' AND (i.request_id=NEW.request_id OR
                      (NEW.aggregate_type IN ('stock_operation_command_seal','stock_operation_return_inbound_seal') AND i.request_id=NEW.after_jsonb->>'request_id')))
                  OR (NEW.stream_key='inventory' AND NEW.request_id='inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(i.request_id,'UTF8')),'hex')))
            LOOP PERFORM public.rsc_check_stock_return_inbound_0111(fact.id); END LOOP;
        ELSIF TG_TABLE_NAME='state_transition_events' AND NEW.aggregate_type='inventory_transaction' THEN
            FOR fact IN SELECT * FROM public.stock_operation_return_inbounds i WHERE i.actor_user_id=NEW.actor_id
                AND NEW.metadata_jsonb->>'request_reference'='inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(i.request_id,'UTF8')),'hex')
            LOOP PERFORM public.rsc_check_stock_return_inbound_0111(fact.id); END LOOP;
        END IF;
    END IF;
    IF checked_tx IS NOT NULL THEN
        SELECT * INTO tx FROM public.inventory_transactions WHERE id=checked_tx;
        IF EXISTS (SELECT 1 FROM public.stock_operation_return_inbounds WHERE posting_transaction_id=tx.reversed_transaction_id) THEN
            RAISE EXCEPTION '0111 inbound cannot use generic reversal' USING ERRCODE='23514';
        END IF;
        SELECT id INTO identifier FROM public.stock_operation_return_inbounds WHERE posting_transaction_id=checked_tx;
        IF identifier IS NULL THEN
            IF tx.source_document_type='stock_return_receipt_inbound' OR tx.posting_key LIKE 'stock-return-receipt-inbound:%' THEN
                RAISE EXCEPTION '0111 detached inbound inventory evidence' USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END IF;
    END IF;
    IF identifier IS NOT NULL THEN
        PERFORM public.rsc_check_stock_return_inbound_0111(identifier);
        IF TG_TABLE_NAME='stock_operation_return_inbounds' THEN
            SELECT * INTO fact FROM public.stock_operation_return_inbounds WHERE id=identifier;
            PERFORM public.rsc_check_return_receiver_0105(fact.shipment_id,fact.actor_user_id,
                fact.operator_person_id,fact.authorization_version,fact.created_at);
        END IF;
    END IF;
    RETURN NULL;
END;
"""

# Replace only this exact branch of the recorded 0103 dispatcher. Every new
# inbound must pass the new proof; a source-type string alone grants nothing.
OLD_BRANCH = """        ELSIF EXISTS (SELECT 1 FROM public.inventory_movements movement
            JOIN public.stock_operation_outbound_lines detail ON detail.transit_stock_account_id IN (movement.from_account_id,movement.to_account_id)"""
NEW_BRANCH = """        ELSIF tx.source_document_type='stock_return_receipt_inbound' THEN
            SELECT id INTO checked_outbound FROM public.stock_operation_return_inbounds WHERE posting_transaction_id=tx.id;
            PERFORM public.rsc_check_stock_return_inbound_0111(checked_outbound);
            RETURN NULL;
""" + OLD_BRANCH.replace("ELSIF EXISTS (", "ELSIF EXISTS( ")

FUNCTIONS = {
    ("rsc_check_stock_return_inbound_0111", "uuid"): ("checked_inbound uuid", "void", CHECK_BODY),
    ("rsc_dispatch_stock_return_inbound_0111", ""): ("", "trigger", DISPATCH_BODY),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key,value in FUNCTIONS.items()}
TRIGGERS = {f"trg_{table}_proof_0111": (table,"rsc_dispatch_stock_return_inbound_0111") for table in (
    "stock_operation_return_inbounds", "stock_operation_return_inbound_lines", "stock_operation_return_inbound_serials",
    "stock_operation_return_inbound_postings", "inventory_transactions", "inventory_movements", "inventory_movement_serials",
    "audit_events", "state_transition_events", "outbox_events")}


def _sources():
    previous=runpy.run_path(str(Path(__file__).with_name("20261013_0103_stock_return_outbounds.py")))['DISPATCH_BODY']
    assert previous.count(OLD_BRANCH)==1
    return {"public.rsc_dispatch_stock_return_outbound_0103()": (previous,previous.replace(OLD_BRANCH,NEW_BRANCH))}


def _transition(upgrade):
    dialect=op.get_bind().dialect.name
    if dialect not in {"postgresql","sqlite"}: raise RuntimeError("0111 supports PostgreSQL and SQLite only")
    folder=Path(__file__).parent
    helper=runpy.run_path(str(folder / "20260927_0087_inbound_fulfillment_boundary.py"))
    helper['_begin_sqlite']()
    if dialect=='postgresql':
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE " + ', '.join('public.'+table for table in sorted({table for table,_ in TRIGGERS.values()} | {'inventory_ledger_heads','stock_operation_receipts','stock_operation_receipt_lines','stock_operation_receipt_serials'})) + " IN SHARE ROW EXCLUSIVE MODE")
    helper['_preflight']("EXISTS (SELECT 1 FROM stock_operation_return_inbounds) OR EXISTS (SELECT 1 FROM inventory_transactions WHERE source_document_type='stock_return_receipt_inbound' OR posting_key LIKE 'stock-return-receipt-inbound:%')",
        "0111 pre-existing inbound facts require investigation before upgrade" if upgrade else "0111 downgrade blocked: inbound proof must be retained")
    if dialect=='sqlite': return
    replace=runpy.run_path(str(folder / "20260912_0072_outbound_postings.py"))['_previous']()['_previous']()['_previous']()['_replace_function_source']
    previous,after=_sources()["public.rsc_dispatch_stock_return_outbound_0103()"]
    if upgrade:
        for (name,signature),(args,result,body) in FUNCTIONS.items():
            op.execute(f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
            op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
        for name,(table,function) in TRIGGERS.items():
            op.execute(f"CREATE CONSTRAINT TRIGGER {name} AFTER INSERT ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.{function}()")
            op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")
    replace(signature="public.rsc_dispatch_stock_return_outbound_0103()",
        expected_hash=hashlib.sha256((previous if upgrade else after).encode()).hexdigest(),
        replacement_hash=hashlib.sha256((after if upgrade else previous).encode()).hexdigest(),
        replacements=((OLD_BRANCH,NEW_BRANCH),) if upgrade else ((NEW_BRANCH,OLD_BRANCH),),label="return_inbound_dispatch_0111")
    if not upgrade:
        for name,(table,_) in TRIGGERS.items(): op.execute(f"DROP TRIGGER {name} ON public.{table}")
        for name,signature in reversed(FUNCTIONS):
            op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p
                WHERE p.oid='public.{name}({signature})'::regprocedure
                  AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{FUNCTION_HASHES[(name,signature)]}'
                  AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND p.prosecdef
                  AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                  AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE acl.grantee<>p.proowner))
                THEN RAISE EXCEPTION '0111 function source, ownership or ACL drift'; END IF; END $body$""")
            op.execute(f"DROP FUNCTION public.{name}({signature})")
    replace(signature="public.rsc_oam_runtime_binding_ready_0044()",expected_hash=OLD_HASH if upgrade else NEW_HASH,
        replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),label="return_inbound_readiness_0111")


def upgrade(): _transition(True)
def downgrade(): _transition(False)
