"""Immutable carrier parcels for exact physical return departure lines."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import runpy
from uuid import UUID
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261014_0104'
down_revision = '20261013_0103'
branch_labels = depends_on = None
OLD_HASH = '24173a4f3c9cf3754b52559e15020694d268e21d022e0205a747937972f083f2'
NEW_HASH = 'df90a6074eb9f653575d68ffbc3e7095e0377d3c18c8cb1559dcf4eb6903419c'
TABLES = ('stock_operation_shipments', 'stock_operation_shipment_lines', 'stock_operation_shipment_serials')

CHECK_BODY = """
DECLARE
    parcel public.stock_operation_shipments%ROWTYPE;
    header public.shipments%ROWTYPE;
    parent public.stock_operation_orders%ROWTYPE;
    line public.stock_operation_shipment_lines%ROWTYPE;
    departure_line public.stock_operation_outbound_lines%ROWTYPE;
    departure public.stock_operation_outbounds%ROWTYPE;
    origin public.stock_operation_lines%ROWTYPE;
    account public.stock_accounts%ROWTYPE;
    policy public.material_inventory_policies%ROWTYPE;
    sku public.materials%ROWTYPE;
    assignment public.custody_assignments%ROWTYPE;
    prior numeric; assigned numeric; held numeric; selected_total numeric;
    ordinal integer := 0; cursor_value bigint; audit_cursor bigint;
    selected jsonb; view jsonb; identifiers jsonb; serial_view jsonb;
    expected_lines jsonb := '[]'::jsonb; expected_intent jsonb; body jsonb;
    fingerprint jsonb; reference text;
BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0104 ledger head missing' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.audit_chain_heads WHERE stream_key='material_request' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0104 audit head missing' USING ERRCODE='23514'; END IF;
    SELECT * INTO parcel FROM public.stock_operation_shipments WHERE id=checked_shipment;
    IF NOT FOUND THEN RAISE EXCEPTION '0104 detached return parcel' USING ERRCODE='23514'; END IF;
    SELECT * INTO header FROM public.shipments WHERE id=parcel.id;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=parcel.operation_id;
    IF header.id IS NULL OR parent.id IS NULL
       OR header.status<>'shipped' OR header.actor_user_id<>parcel.actor_user_id
       OR parcel.actor_user_id<>parent.actor_user_id OR header.actor_person_id<>parent.requester_id
       OR header.source_location_id<>parent.source_location_id OR header.target_location_id<>parent.target_location_id
       OR header.authorization_version<1 OR parcel.audit_version<1
       OR header.idempotency_key_hash !~ '^[a-f0-9]{64}$' OR parcel.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
       OR header.shipment_no<>'RET-SHP-' || upper(substr(header.idempotency_key_hash,1,24))
       OR length(btrim(parcel.reason,E' \\t\\r\\n')) NOT BETWEEN 1 AND 500 OR btrim(parcel.reason,E' \\t\\r\\n')<>parcel.reason
       OR length(btrim(header.carrier)) NOT BETWEEN 1 AND 100 OR btrim(header.carrier)<>header.carrier
       OR EXISTS (SELECT 1 FROM regexp_split_to_table(header.carrier,'') letter(value) WHERE ascii(letter.value)<32 OR ascii(letter.value)=127)
       OR length(btrim(header.tracking_no)) NOT BETWEEN 1 AND 100 OR btrim(header.tracking_no)<>header.tracking_no
       OR EXISTS (SELECT 1 FROM regexp_split_to_table(header.tracking_no,'') letter(value) WHERE ascii(letter.value)<32 OR ascii(letter.value)=127)
       OR header.created_at<>parcel.created_at OR parcel.created_at>clock_timestamp()
       OR header.shipped_at>parcel.created_at OR header.shipped_at<parent.created_at
       OR NOT EXISTS (SELECT 1 FROM public.users actor JOIN public.people person ON person.id=actor.person_id
            WHERE actor.id=header.actor_user_id AND actor.person_id=header.actor_person_id
              AND actor.authorization_version=header.authorization_version AND actor.is_active
              AND actor.account_status='active' AND person.employment_status='active')
       OR EXISTS (SELECT 1 FROM public.stock_operation_cancellations WHERE operation_id=parent.id)
       OR EXISTS (SELECT 1 FROM public.stock_operation_command_seals WHERE actor_user_id=parcel.actor_user_id AND request_id=parcel.request_id)
       OR (SELECT count(*) FROM (SELECT actor_user_id,request_id FROM public.stock_operation_orders
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_cancellations
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_outbounds
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_shipments) requests
            WHERE requests.actor_user_id=parcel.actor_user_id AND requests.request_id=parcel.request_id)<>1
       OR jsonb_typeof(parcel.command_jsonb) IS DISTINCT FROM 'object' OR jsonb_typeof(parcel.plan_jsonb) IS DISTINCT FROM 'object'
       OR jsonb_typeof(parcel.plan_jsonb->'lines') IS DISTINCT FROM 'array' OR jsonb_typeof(parcel.plan_jsonb->'policies') IS DISTINCT FROM 'array'
       OR (SELECT count(*) FROM jsonb_object_keys(parcel.plan_jsonb))<>8
       OR parcel.plan_jsonb->'intent' IS DISTINCT FROM parcel.command_jsonb
       OR parcel.plan_jsonb->'authorization_version' IS DISTINCT FROM to_jsonb(header.authorization_version)
       OR parcel.plan_jsonb->>'original_request_hash' IS DISTINCT FROM parent.request_hash
       OR header.request_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(parcel.command_jsonb),'UTF8')),'hex')
       OR parcel.plan_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(parcel.plan_jsonb),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0104 parcel identity, original return or request mismatch' USING ERRCODE='23514';
    END IF;
    cursor_value := (parcel.plan_jsonb->>'ledger_cursor')::bigint;
    audit_cursor := (parcel.plan_jsonb->>'audit_cursor')::bigint;
    IF cursor_value IS NULL OR cursor_value<1 OR audit_cursor IS NULL OR audit_cursor+1<>parcel.audit_version
       OR parcel.plan_jsonb->'ledger_cursor' IS DISTINCT FROM to_jsonb(cursor_value)
       OR parcel.plan_jsonb->'audit_cursor' IS DISTINCT FROM to_jsonb(audit_cursor)
       OR cursor_value<>(SELECT next_cursor-1 FROM public.inventory_ledger_heads WHERE stream_key='inventory')
       OR parcel.audit_version>(SELECT version FROM public.audit_chain_heads WHERE stream_key='material_request') THEN
        RAISE EXCEPTION '0104 parcel ledger or audit cursor mismatch' USING ERRCODE='23514';
    END IF;
    SELECT * INTO assignment FROM public.custody_assignments WHERE id=parcel.target_custody_assignment_id;
    IF assignment.id IS NULL OR assignment.location_id<>parent.target_location_id
       OR assignment.custodian_person_id IS DISTINCT FROM header.target_person_id
       OR assignment.valid_from>header.shipped_at OR (assignment.valid_to IS NOT NULL AND assignment.valid_to<=parcel.created_at)
       OR NOT EXISTS (SELECT 1 FROM public.stock_locations source
            JOIN public.stock_locations target ON target.id=parent.target_location_id
            JOIN public.stock_locations transit ON transit.id=parent.transit_location_id
            WHERE source.id=parent.source_location_id AND source.status='active' AND source.location_type='personal'
              AND source.custodian_person_id=header.actor_person_id AND source.parent_id=target.id
              AND target.status='active' AND target.location_type='region' AND target.owner_org_id=source.owner_org_id
              AND target.custodian_person_id=assignment.custodian_person_id
              AND transit.status='active' AND transit.location_type='transit' AND transit.parent_id=target.id
              AND transit.owner_org_id=target.owner_org_id
              AND parcel.plan_jsonb->'destination'->>'source_location_id'=source.id::text
              AND parcel.plan_jsonb->'destination'->>'target_location_id'=target.id::text
              AND parcel.plan_jsonb->'destination'->>'transit_location_id'=transit.id::text
              AND parcel.plan_jsonb->'destination'->>'region_org_id'=target.owner_org_id::text
              AND parcel.plan_jsonb->'destination'->>'custody_assignment_id'=assignment.id::text
              AND parcel.plan_jsonb->'destination'->>'custodian_person_id'=assignment.custodian_person_id::text
              AND (parcel.plan_jsonb->'destination'->>'custody_effective_from')::timestamptz=assignment.valid_from) THEN
        RAISE EXCEPTION '0104 parcel destination or receiving responsibility mismatch' USING ERRCODE='23514';
    END IF;
    FOR line IN SELECT * FROM public.stock_operation_shipment_lines WHERE shipment_id=parcel.id ORDER BY line_no LOOP
        ordinal := ordinal+1;
        SELECT * INTO departure_line FROM public.stock_operation_outbound_lines WHERE id=line.outbound_line_id;
        SELECT * INTO departure FROM public.stock_operation_outbounds WHERE id=departure_line.outbound_id;
        SELECT * INTO origin FROM public.stock_operation_lines WHERE id=departure_line.operation_line_id;
        SELECT * INTO account FROM public.stock_accounts WHERE id=departure_line.transit_stock_account_id;
        SELECT * INTO sku FROM public.materials WHERE id=account.material_id;
        selected := parcel.command_jsonb->'lines'->(ordinal-1); view := parcel.plan_jsonb->'lines'->(ordinal-1);
        IF line.line_no<>ordinal OR departure.id IS NULL OR account.id IS NULL OR origin.id IS NULL OR sku.id IS NULL
           OR departure.operation_id<>parent.id OR origin.operation_id<>parent.id
           OR departure.actor_user_id<>parcel.actor_user_id OR departure.operator_person_id<>header.actor_person_id
           OR departure.outbound_at>header.shipped_at OR departure.created_at>parcel.created_at
           OR account.location_id<>parent.transit_location_id OR account.availability_bucket<>'in_transit'
           OR account.custodian_person_id IS DISTINCT FROM header.actor_person_id OR account.condition_code NOT IN ('used','damaged')
           OR account.material_id<>origin.material_id OR sku.status<>'active'
           OR NOT EXISTS (SELECT 1 FROM public.inventory_transactions tx WHERE tx.id=departure.posting_transaction_id
                AND tx.status='posted' AND tx.source_document_type='stock_operation_return_outbound'
                AND tx.source_document_id=departure.id::text AND tx.ledger_cursor<=cursor_value)
           OR EXISTS (SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id=departure.posting_transaction_id) THEN
            RAISE EXCEPTION '0104 exact departure or in-transit account mismatch' USING ERRCODE='23514';
        END IF;
        SELECT COALESCE(sum(prior_line.quantity),0) INTO prior FROM public.stock_operation_shipment_lines prior_line
            JOIN public.stock_operation_shipments prior_parcel ON prior_parcel.id=prior_line.shipment_id
            WHERE prior_line.outbound_line_id=departure_line.id AND prior_parcel.audit_version<parcel.audit_version;
        SELECT COALESCE(sum(prior_line.quantity),0) INTO assigned FROM public.stock_operation_shipment_lines prior_line
            JOIN public.stock_operation_shipments prior_parcel ON prior_parcel.id=prior_line.shipment_id
            JOIN public.stock_operation_outbound_lines prior_departure ON prior_departure.id=prior_line.outbound_line_id
            WHERE prior_departure.transit_stock_account_id=account.id AND prior_parcel.audit_version<parcel.audit_version;
        SELECT COALESCE(sum(CASE WHEN movement.to_account_id=account.id THEN movement.quantity ELSE -movement.quantity END),0)
            INTO held FROM public.inventory_movements movement JOIN public.inventory_transactions tx ON tx.id=movement.transaction_id
            WHERE account.id IN (movement.from_account_id,movement.to_account_id) AND tx.ledger_cursor<=cursor_value;
        SELECT sum(selected_line.quantity) INTO selected_total FROM public.stock_operation_shipment_lines selected_line
            JOIN public.stock_operation_outbound_lines selected_departure ON selected_departure.id=selected_line.outbound_line_id
            WHERE selected_line.shipment_id=parcel.id AND selected_departure.transit_stock_account_id=account.id;
        IF line.quantity>departure_line.quantity-prior OR selected_total>held-assigned THEN
            RAISE EXCEPTION '0104 parcel exceeds exact departure or unassigned transit budget' USING ERRCODE='23514';
        END IF;
        SELECT COALESCE(jsonb_agg(sn.serial_id::text ORDER BY sn.serial_id),'[]'::jsonb),
            COALESCE(jsonb_agg(jsonb_build_object('serial_id',sn.serial_id::text,'serial_no',serial.serial_no) ORDER BY sn.serial_id),'[]'::jsonb)
            INTO identifiers,serial_view FROM public.stock_operation_shipment_serials sn
            JOIN public.inventory_serials serial ON serial.id=sn.serial_id WHERE sn.line_id=line.id;
        IF EXISTS (SELECT 1 FROM public.stock_operation_shipment_serials sn
            LEFT JOIN public.stock_operation_outbound_serials proof ON proof.line_id=departure_line.id AND proof.serial_id=sn.serial_id
            LEFT JOIN public.inventory_serials serial ON serial.id=sn.serial_id
            LEFT JOIN public.serial_current_positions position ON position.serial_id=sn.serial_id
            WHERE sn.line_id=line.id AND (sn.outbound_line_id<>departure_line.id OR proof.id IS NULL OR NOT proof.sku_verified OR NOT proof.qr_verified
                OR serial.id IS NULL OR serial.material_id<>account.material_id OR serial.lot_id IS DISTINCT FROM account.lot_id
                OR serial.lifecycle_status<>'active' OR position.stock_account_id IS DISTINCT FROM account.id))
           OR EXISTS (SELECT 1 FROM public.stock_operation_shipment_serials all_sn
                JOIN public.stock_operation_shipment_lines all_lines ON all_lines.id=all_sn.line_id
                WHERE all_lines.shipment_id=parcel.id GROUP BY all_sn.serial_id HAVING count(*)>1) THEN
            RAISE EXCEPTION '0104 serial not in its original unshipped departure' USING ERRCODE='23514';
        END IF;
        SELECT * INTO policy FROM public.material_inventory_policies WHERE material_id=account.material_id
            AND effective_from<=parcel.created_at AND (effective_to IS NULL OR effective_to>parcel.created_at);
        IF policy.id IS NULL OR (SELECT count(*) FROM public.material_inventory_policies WHERE material_id=account.material_id
            AND effective_from<=parcel.created_at AND (effective_to IS NULL OR effective_to>parcel.created_at))<>1
           OR line.quantity<>round(line.quantity,policy.quantity_scale)
           OR (NOT policy.allow_fraction AND line.quantity<>trunc(line.quantity))
           OR (policy.tracking_mode IN ('none','serial') AND account.lot_id IS NOT NULL)
           OR (policy.tracking_mode IN ('lot','lot_and_serial') AND account.lot_id IS NULL)
           OR (policy.tracking_mode IN ('serial','lot_and_serial') AND jsonb_array_length(identifiers)<>line.quantity)
           OR (policy.tracking_mode IN ('none','lot') AND jsonb_array_length(identifiers)<>0) THEN
            RAISE EXCEPTION '0104 parcel quantity, lot or serial policy mismatch' USING ERRCODE='23514';
        END IF;
        SELECT item INTO fingerprint FROM jsonb_array_elements(parcel.plan_jsonb->'policies') item WHERE item->>0=account.material_id::text;
        IF fingerprint IS NULL OR jsonb_array_length(fingerprint)<>7 OR fingerprint->>1<>policy.id::text
           OR fingerprint->>2<>policy.tracking_mode OR fingerprint->3 IS DISTINCT FROM to_jsonb(policy.quantity_scale)
           OR fingerprint->4 IS DISTINCT FROM to_jsonb(policy.allow_fraction) OR (fingerprint->>5)::timestamptz<>policy.effective_from
           OR (fingerprint->>6)::timestamptz IS DISTINCT FROM policy.effective_to THEN
            RAISE EXCEPTION '0104 parcel policy snapshot mismatch' USING ERRCODE='23514';
        END IF;
        IF view IS DISTINCT FROM jsonb_build_object('outbound_id',departure.id::text,'outbound_no',departure.outbound_no,
            'outbound_line_id',departure_line.id::text,'operation_line_id',origin.id::text,'source_recovery_line_id',origin.source_recovery_line_id::text,
            'transit_stock_account_id',account.id::text,'material_id',account.material_id::text,'sku_code',sku.sku_code,'material_name',sku.name,
            'base_unit',sku.base_unit,'condition_code',account.condition_code,'lot_id',account.lot_id::text,
            'lot_no',(SELECT lot_no FROM public.inventory_lots WHERE id=account.lot_id),
            'outbound_quantity',to_char(departure_line.quantity,'FM999999999999990.000'),'shipped_quantity',to_char(prior,'FM999999999999990.000'),
            'unshipped_quantity',to_char(departure_line.quantity-prior,'FM999999999999990.000'),'in_transit_quantity',to_char(held,'FM999999999999990.000'),
            'unassigned_quantity',to_char(held-assigned,'FM999999999999990.000'),'selected_quantity',to_char(line.quantity,'FM999999999999990.000'),
            'selected_serials',serial_view) THEN
            RAISE EXCEPTION '0104 parcel preview facts mismatch' USING ERRCODE='23514';
        END IF;
        expected_lines := expected_lines || jsonb_build_array(jsonb_build_object('outbound_line_id',departure_line.id::text,
            'quantity',to_char(line.quantity,'FM999999999999990.000'),'serial_ids',identifiers));
    END LOOP;
    IF ordinal NOT BETWEEN 1 AND 100 OR jsonb_array_length(parcel.plan_jsonb->'lines')<>ordinal
       OR jsonb_array_length(parcel.plan_jsonb->'policies')<>(SELECT count(DISTINCT current_account.material_id)
            FROM public.stock_operation_shipment_lines current_line JOIN public.stock_operation_outbound_lines current_departure ON current_departure.id=current_line.outbound_line_id
            JOIN public.stock_accounts current_account ON current_account.id=current_departure.transit_stock_account_id WHERE current_line.shipment_id=parcel.id) THEN
        RAISE EXCEPTION '0104 incomplete parcel lines or policies' USING ERRCODE='23514';
    END IF;
    SELECT jsonb_agg(item ORDER BY item->>'outbound_line_id') INTO expected_lines FROM jsonb_array_elements(expected_lines) item;
    expected_intent := jsonb_build_object('operation_type','ship_return','operation_id',parent.id::text,'operator_person_id',header.actor_person_id::text,
        'carrier',header.carrier,'tracking_no',header.tracking_no,'shipped_at',to_char(header.shipped_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'reason',parcel.reason,'lines',expected_lines);
    reference := 'inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(parcel.request_id,'UTF8')),'hex');
    IF parcel.command_jsonb IS DISTINCT FROM expected_intent
       OR EXISTS (SELECT 1 FROM public.shipment_lines WHERE shipment_id=parcel.id)
       OR EXISTS (SELECT 1 FROM public.logistics_events WHERE shipment_id=parcel.id)
       OR EXISTS (SELECT 1 FROM public.receipts WHERE shipment_id=parcel.id)
       OR EXISTS (SELECT 1 FROM public.inventory_transactions WHERE idempotency_key_hash=header.idempotency_key_hash
            OR (source_document_type='stock_operation_return_shipment' AND source_document_id=parcel.id::text))
       OR EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='inventory' AND actor_user_id=parcel.actor_user_id AND request_id=reference)
       OR EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type='inventory_transaction' AND actor_id=parcel.actor_user_id
            AND metadata_jsonb->>'request_reference'=reference) THEN
        RAISE EXCEPTION '0104 parcels cannot bypass typed fulfillment or post stock' USING ERRCODE='23514';
    END IF;
    body := jsonb_build_object('operation_id',parent.id::text,'shipment_id',parcel.id::text,'work_order_id',parent.oam_work_order_id::text,
        'operator_person_id',header.actor_person_id::text,'request_hash',header.request_hash);
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='material_request' AND actor_user_id=parcel.actor_user_id AND request_id=parcel.request_id)<>1
       OR NOT EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='material_request' AND stream_version=parcel.audit_version
            AND aggregate_type='stock_operation_shipment' AND aggregate_id=parcel.id::text AND actor_user_id=parcel.actor_user_id
            AND request_id=parcel.request_id AND action='stock_return_shipped' AND before_jsonb='{}'::jsonb AND after_jsonb=body
            AND occurred_at=parcel.created_at AND created_at=parcel.created_at)
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type='stock_operation_shipment' AND aggregate_id=parcel.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.outbox_events WHERE aggregate_type='stock_operation_shipment' AND aggregate_id=parcel.id::text
            AND event_type='stock_return_shipped' AND payload_jsonb=body AND idempotency_key='stock_return_shipped:' || parcel.id::text)
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type='stock_operation_shipment' AND aggregate_id=parcel.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type='stock_operation_shipment' AND aggregate_id=parcel.id::text
            AND from_status IS NULL AND to_status='shipped' AND actor_id=parcel.actor_user_id AND reason='stock_return_shipped'
            AND metadata_jsonb=body AND idempotency_key='stock_return_shipped:' || parcel.id::text) THEN
        RAISE EXCEPTION '0104 complete atomic parcel audit, state and outbox required' USING ERRCODE='23514';
    END IF;
END;
"""

DISPATCH_BODY = """
DECLARE identifier uuid;
BEGIN
    IF TG_TABLE_NAME='stock_operation_shipments' THEN identifier := NEW.id;
    ELSIF TG_TABLE_NAME='stock_operation_shipment_lines' THEN identifier := NEW.shipment_id;
    ELSIF TG_TABLE_NAME='stock_operation_shipment_serials' THEN
        SELECT shipment_id INTO identifier FROM public.stock_operation_shipment_lines WHERE id=NEW.line_id;
    ELSIF TG_TABLE_NAME='shipments' THEN
        IF NOT EXISTS (SELECT 1 FROM public.stock_operation_shipments WHERE id=NEW.id) AND NEW.shipment_no NOT LIKE 'RET-SHP-%' THEN RETURN NULL; END IF;
        identifier := NEW.id;
    ELSIF TG_TABLE_NAME IN ('shipment_lines','logistics_events','receipts') THEN
        IF EXISTS (SELECT 1 FROM public.stock_operation_shipments WHERE id=NEW.shipment_id) THEN
            RAISE EXCEPTION '0104 return parcels require typed downstream documents' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    ELSIF TG_TABLE_NAME='inventory_transactions' THEN
        IF NEW.source_document_type='stock_operation_return_shipment' OR EXISTS (SELECT 1 FROM public.stock_operation_shipments parcel
            JOIN public.shipments header ON header.id=parcel.id WHERE header.idempotency_key_hash=NEW.idempotency_key_hash) THEN
            RAISE EXCEPTION '0104 carrier parcel registration cannot post inventory' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    ELSE
        IF NEW.aggregate_type='stock_operation_shipment' THEN identifier := NEW.aggregate_id::uuid;
        ELSIF TG_TABLE_NAME='audit_events' THEN
            SELECT parcel.id INTO identifier FROM public.stock_operation_shipments parcel WHERE parcel.actor_user_id=NEW.actor_user_id
                AND ((NEW.stream_key='material_request' AND parcel.request_id=NEW.request_id)
                  OR (NEW.stream_key='inventory' AND NEW.request_id='inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(parcel.request_id,'UTF8')),'hex')));
            IF identifier IS NULL THEN RETURN NULL; END IF;
        ELSIF TG_TABLE_NAME='state_transition_events' AND NEW.aggregate_type='inventory_transaction' THEN
            SELECT parcel.id INTO identifier FROM public.stock_operation_shipments parcel WHERE parcel.actor_user_id=NEW.actor_id
                AND NEW.metadata_jsonb->>'request_reference'='inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(parcel.request_id,'UTF8')),'hex');
            IF identifier IS NULL THEN RETURN NULL; END IF;
        ELSE RETURN NULL; END IF;
    END IF;
    IF identifier IS NULL THEN RAISE EXCEPTION '0104 detached parcel evidence' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_stock_return_shipment_0104(identifier);
    RETURN NULL;
END;
"""


def _replace_once(body, old, new):
    if body.count(old) != 1: raise RuntimeError('0104 historical source anchor drift')
    return body.replace(old, new, 1)


def _sources():
    prior = runpy.run_path(str(Path(__file__).with_name('20261013_0103_stock_return_outbounds.py')))
    result = {}
    for signature, old in (
        ('public.rsc_check_stock_return_0100(uuid, uuid)', prior['_sources']()['public.rsc_check_stock_return_0100(uuid, uuid)'][1]),
        ('public.rsc_check_stock_return_outbound_0103(uuid)', prior['CHECK_BODY']),
    ):
        anchor = 'UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_outbounds) requests' if '0103(' in signature else 'UNION ALL SELECT actor_user_id, request_id FROM public.stock_operation_outbounds) requests'
        result[signature] = (old, _replace_once(old, anchor, anchor.replace(') requests', '\n            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_shipments) requests')))
    old = prior['_sources']()['public.rsc_guard_stock_operation_seal_0101()'][1]
    new = _replace_once(old, "'stock_operation_cancellations','stock_operation_outbounds')", "'stock_operation_cancellations','stock_operation_outbounds','stock_operation_shipments')")
    new = _replace_once(new, "NOT IN ('submit_return','cancel_return','outbound_return')", "NOT IN ('submit_return','cancel_return','outbound_return','ship_return')")
    new = _replace_once(new, "IN ('cancel_return','outbound_return') AND NOT EXISTS", "IN ('cancel_return','outbound_return','ship_return') AND NOT EXISTS")
    new = _replace_once(new, "       OR EXISTS (SELECT 1 FROM public.audit_events event WHERE event.actor_user_id=seal.actor_user_id", """       OR EXISTS (SELECT 1 FROM public.stock_operation_shipments parcel
            WHERE parcel.actor_user_id=seal.actor_user_id AND parcel.request_id=seal.request_id)
       OR EXISTS (SELECT 1 FROM public.audit_events event WHERE event.actor_user_id=seal.actor_user_id""")
    new = _replace_once(new, "'stock_operation_cancellation','stock_operation_outbound')", "'stock_operation_cancellation','stock_operation_outbound','stock_operation_shipment')")
    result['public.rsc_guard_stock_operation_seal_0101()'] = (old, new)
    return result


FUNCTIONS = {
    ('rsc_check_stock_return_shipment_0104', 'uuid'): ('checked_shipment uuid', 'void', CHECK_BODY),
    ('rsc_dispatch_stock_return_shipment_0104', ''): ('', 'trigger', DISPATCH_BODY),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}
TRIGGERS = {}
for _table in TABLES:
    TRIGGERS[f'trg_{_table}_proof_0104'] = (_table, 'INSERT', 'rsc_dispatch_stock_return_shipment_0104', 5, True)
    TRIGGERS[f'trg_{_table}_immutable_0104'] = (_table, 'UPDATE OR DELETE', 'rsc_guard_work_order_facts_0090', 27, False)
    TRIGGERS[f'trg_{_table}_no_truncate_0104'] = (_table, 'TRUNCATE', 'rsc_guard_work_order_facts_0090', 34, False)
for _table in ('shipments','shipment_lines','logistics_events','receipts','inventory_transactions','audit_events','outbox_events','state_transition_events'):
    TRIGGERS[f'trg_{_table}_return_shipment_0104'] = (_table, 'INSERT', 'rsc_dispatch_stock_return_shipment_0104', 5, True)
TRIGGERS['trg_stock_operation_shipments_seal_0104'] = (TABLES[0], 'INSERT', 'rsc_guard_stock_operation_seal_0101', 5, True)


def _create_tables():
    document = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
    op.create_table(TABLES[0],
        sa.Column('id',sa.Uuid(),sa.ForeignKey('shipments.id',ondelete='RESTRICT'),primary_key=True),
        sa.Column('operation_id',sa.Uuid(),sa.ForeignKey('stock_operation_orders.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('target_custody_assignment_id',sa.Uuid(),sa.ForeignKey('custody_assignments.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('request_id',sa.String(160),nullable=False),sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('plan_hash',sa.String(64),nullable=False),sa.Column('audit_version',sa.BigInteger(),nullable=False),
        sa.Column('command_jsonb',document,nullable=False),sa.Column('plan_jsonb',document,nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('actor_user_id','request_id',name='uq_stock_operation_shipments_request'),
        sa.UniqueConstraint('audit_version',name='uq_stock_operation_shipments_audit'),
        sa.CheckConstraint('audit_version > 0',name='ck_stock_operation_shipments_audit'),
        sa.CheckConstraint('length(reason) BETWEEN 1 AND 500 AND length(plan_hash)=64',name='ck_stock_operation_shipments_context'))
    op.create_index('ix_stock_operation_shipments_operation_id',TABLES[0],['operation_id'])
    op.create_table(TABLES[1],
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('shipment_id',sa.Uuid(),sa.ForeignKey(TABLES[0]+'.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('line_no',sa.BigInteger(),nullable=False),sa.Column('outbound_line_id',sa.Uuid(),sa.ForeignKey('stock_operation_outbound_lines.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('quantity',sa.Numeric(18,3),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('shipment_id','line_no',name='uq_stock_operation_shipment_lines_order'),
        sa.UniqueConstraint('shipment_id','outbound_line_id',name='uq_stock_operation_shipment_lines_origin'),
        sa.UniqueConstraint('id','outbound_line_id',name='uq_stock_operation_shipment_lines_binding'),
        sa.CheckConstraint('line_no > 0 AND quantity > 0',name='ck_stock_operation_shipment_lines_quantity'))
    op.create_index('ix_stock_operation_shipment_lines_outbound_line_id',TABLES[1],['outbound_line_id'])
    op.create_table(TABLES[2],
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('line_id',sa.Uuid(),nullable=False),sa.Column('outbound_line_id',sa.Uuid(),nullable=False),
        sa.Column('serial_id',sa.Uuid(),sa.ForeignKey('inventory_serials.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('line_id','serial_id',name='uq_stock_operation_shipment_serials_line'),
        sa.UniqueConstraint('outbound_line_id','serial_id',name='uq_stock_operation_shipment_serials_origin'),
        sa.ForeignKeyConstraint(['line_id','outbound_line_id'],[TABLES[1]+'.id',TABLES[1]+'.outbound_line_id'],ondelete='RESTRICT'))


def _permissions(upgrade):
    now=datetime.now(timezone.utc); identifier=UUID('20000000-0000-4000-8000-000000000067')
    permissions=sa.table('permissions',sa.column('id',sa.Uuid()),sa.column('resource',sa.String()),sa.column('action',sa.String()),
        sa.column('field_code',sa.String()),sa.column('description',sa.String()),sa.column('created_at',sa.DateTime()),sa.column('updated_at',sa.DateTime()))
    grants=sa.table('role_permissions',sa.column('id',sa.Uuid()),sa.column('role_id',sa.Uuid()),sa.column('permission_id',sa.Uuid()),
        sa.column('effect',sa.String()),sa.column('created_at',sa.DateTime()))
    rows=[dict(id=UUID(f'21000000-0000-4000-8000-{123+role:012d}'),role_id=UUID(f'10000000-0000-4000-8000-{role:012d}'),
        permission_id=identifier,effect='allow',created_at=now) for role in (1,2,3)]
    if upgrade:
        op.bulk_insert(permissions,[dict(id=identifier,resource='stock_operation',action='ship_return',field_code='',
            description='Formal carrier parcels for return departures',created_at=now,updated_at=now)])
        op.bulk_insert(grants,rows)
    else:
        op.execute(grants.delete().where(grants.c.id.in_([op.inline_literal(row['id'],type_=sa.Uuid()) for row in rows])))
        op.execute(permissions.delete().where(permissions.c.id==op.inline_literal(identifier,type_=sa.Uuid())))


def _seal_constraints(upgrade):
    kinds="'submit_return','cancel_return','outbound_return'"+(",'ship_return'" if upgrade else '')
    origins="'cancel_return','outbound_return'"+(",'ship_return'" if upgrade else '')
    with op.batch_alter_table('stock_operation_command_seals') as batch:
        batch.drop_constraint('ck_stock_operation_seals_type',type_='check');batch.drop_constraint('ck_stock_operation_seals_origin',type_='check')
        batch.create_check_constraint('ck_stock_operation_seals_type',f'operation_type IN ({kinds})')
        batch.create_check_constraint('ck_stock_operation_seals_origin',f"(operation_type='submit_return' AND operation_id IS NULL) OR (operation_type IN ({origins}) AND operation_id IS NOT NULL)")
    if op.get_bind().dialect.name=='sqlite':
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_stock_operation_seals_{action.lower()}_0101 BEFORE {action} ON stock_operation_command_seals BEGIN SELECT RAISE(ABORT, '0101 return request seals are append-only'); END")


def _transition(upgrade):
    db=op.get_bind();folder=Path(__file__).parent
    if db.dialect.name not in {'postgresql','sqlite'}:raise RuntimeError('0104 supports PostgreSQL and SQLite only')
    helper=runpy.run_path(str(folder/'20260927_0087_inbound_fulfillment_boundary.py'));helper['_begin_sqlite']()
    if db.dialect.name=='postgresql':
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads, public.inventory_transactions, public.stock_operation_orders, public.stock_operation_cancellations, public.stock_operation_outbounds, public.stock_operation_command_seals, public.shipments, public.shipment_lines, public.logistics_events, public.receipts, public.audit_events IN SHARE ROW EXCLUSIVE MODE')
    if not upgrade:
        if db.dialect.name=='postgresql':op.execute('LOCK TABLE '+','.join('public.'+table for table in TABLES)+' IN SHARE ROW EXCLUSIVE MODE')
        helper['_preflight'](' OR '.join(f'EXISTS (SELECT 1 FROM {table})' for table in TABLES)+" OR EXISTS (SELECT 1 FROM stock_operation_command_seals WHERE operation_type='ship_return')",
            '0104 downgrade blocked: immutable return parcels or request seals must be retained')
    if upgrade:_create_tables();_permissions(True)
    _seal_constraints(upgrade)
    if db.dialect.name=='postgresql':
        replace=runpy.run_path(str(folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        if upgrade:
            for (name,signature),(args,result,body) in FUNCTIONS.items():
                op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$')
                op.execute(f'REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api')
            for name,(table,events,function,_,deferred) in TRIGGERS.items():
                sql=(f'CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW' if deferred
                    else f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}")
                op.execute(sql+f' EXECUTE FUNCTION public.{function}()');op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
            op.execute('GRANT SELECT, INSERT ON '+','.join('public.'+table for table in TABLES)+' TO star_oam_api')
        else:
            for (name,signature),digest in FUNCTION_HASHES.items():
                op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='public.{name}({signature})'::regprocedure
                    AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{digest}'
                    AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND p.prosecdef
                    AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                    AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl WHERE acl.grantee<>p.proowner))
                    THEN RAISE EXCEPTION '0104 function source, ownership or ACL drift'; END IF; END $body$""")
        for signature,(old,new) in _sources().items():
            replace(signature=signature,expected_hash=hashlib.sha256((old if upgrade else new).encode()).hexdigest(),
                replacement_hash=hashlib.sha256((new if upgrade else old).encode()).hexdigest(),
                replacements=((old,new),) if upgrade else ((new,old),),label='stock_return_shipment_0104')
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),label='stock_return_shipment_readiness_0104')
        if not upgrade:
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name,signature in reversed(FUNCTIONS):op.execute(f'DROP FUNCTION public.{name}({signature})')
    if db.dialect.name=='sqlite' and upgrade:
        for table in TABLES:
            for action in ('UPDATE','DELETE'):
                op.execute(f"CREATE TRIGGER trg_{table}_{action.lower()}_0104 BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, '0104 return parcels are append-only'); END")
    if not upgrade:
        _permissions(False)
        for table in reversed(TABLES):op.drop_table(table)


def upgrade():_transition(True)
def downgrade():_transition(False)
