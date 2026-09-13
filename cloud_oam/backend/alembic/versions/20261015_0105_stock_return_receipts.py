"""Independent, immutable recipient acceptance of exact return parcel lines.

Acceptance records observations and confirmed quantities. It never posts stock.
"""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import runpy
from uuid import UUID

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20261015_0105'
down_revision = '20261014_0104'
branch_labels = depends_on = None
OLD_HASH = 'df90a6074eb9f653575d68ffbc3e7095e0377d3c18c8cb1559dcf4eb6903419c'
NEW_HASH = '65b5e8ea7d0442ecdd289b46bebe0531371e3691aa930d88817e9ceba9a7a240'
TABLES = ('stock_operation_receipts', 'stock_operation_receipt_lines',
    'stock_operation_receipt_serials', 'stock_operation_receipt_exceptions')


def _previous():
    return runpy.run_path(str(Path(__file__).with_name('20261014_0104_stock_return_shipments.py')))


def _replace_once(body, old, new):
    if body.count(old) != 1: raise RuntimeError('0105 historical source anchor drift')
    return body.replace(old, new, 1)


RECEIVER_BODY = """
DECLARE
    header public.shipments%ROWTYPE;
    parcel public.stock_operation_shipments%ROWTYPE;
    location public.stock_locations%ROWTYPE;
    person public.people%ROWTYPE;
    assignment public.role_assignments%ROWTYPE;
    role_code text; organization public.organizations%ROWTYPE;
    has_receiver_role boolean := false;
    permission_row record; allows boolean; denies boolean;
    current_time_value timestamptz := clock_timestamp();
BEGIN
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[checked_actor]);
    SELECT * INTO header FROM public.shipments WHERE id=checked_shipment;
    SELECT * INTO parcel FROM public.stock_operation_shipments WHERE id=checked_shipment;
    PERFORM id FROM public.stock_locations WHERE id=header.target_location_id FOR UPDATE;
    SELECT * INTO location FROM public.stock_locations WHERE id=header.target_location_id;
    PERFORM custody.id FROM public.custody_assignments custody WHERE custody.location_id=location.id
        ORDER BY custody.id FOR UPDATE OF custody;
    PERFORM org.id FROM public.organizations org WHERE org.id IN (
        WITH RECURSIVE ancestors(id,parent_id) AS (
            SELECT id,parent_id FROM public.organizations WHERE id=location.owner_org_id
            UNION SELECT parent.id,parent.parent_id FROM public.organizations parent JOIN ancestors child ON child.parent_id=parent.id
        ) SELECT id FROM ancestors
    ) ORDER BY org.id FOR UPDATE OF org;
    SELECT * INTO person FROM public.people WHERE id=checked_person;
    SELECT * INTO organization FROM public.organizations WHERE id=person.organization_id;
    IF header.id IS NULL OR parcel.id IS NULL OR header.status<>'shipped'
       OR header.target_person_id IS DISTINCT FROM checked_person
       OR checked_at<parcel.created_at OR checked_at>current_time_value
       OR person.id IS NULL OR person.employment_status<>'active'
       OR organization.id IS NULL OR organization.status<>'active'
       OR location.id IS NULL OR location.status<>'active' OR location.location_type<>'region'
       OR location.custodian_person_id IS DISTINCT FROM checked_person
       OR NOT EXISTS (SELECT 1 FROM public.organizations WHERE id=location.owner_org_id AND status='active')
       OR NOT EXISTS (SELECT 1 FROM public.users actor WHERE actor.id=checked_actor AND actor.person_id=checked_person
            AND actor.is_active AND actor.account_status='active' AND actor.authorization_version=checked_version)
       OR NOT EXISTS (SELECT 1 FROM public.auth_identities WHERE user_id=checked_actor AND status='active'
            AND verified_at IS NOT NULL AND verified_at<=current_time_value AND revoked_at IS NULL)
       OR (SELECT count(*) FROM public.custody_assignments WHERE location_id=location.id AND valid_from<=current_time_value
            AND (valid_to IS NULL OR valid_to>current_time_value))<>1
       OR NOT EXISTS (SELECT 1 FROM public.custody_assignments WHERE id=parcel.target_custody_assignment_id
            AND location_id=location.id AND custodian_person_id=checked_person AND valid_from<=checked_at
            AND (valid_to IS NULL OR valid_to>current_time_value)) THEN
        RAISE EXCEPTION '0105 current receiver, identity or custody mismatch' USING ERRCODE='23514';
    END IF;
    FOR assignment IN SELECT a.* FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        WHERE a.user_id=checked_actor AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=current_time_value AND (a.valid_to IS NULL OR a.valid_to>current_time_value) AND r.status='active' LOOP
        SELECT code INTO role_code FROM public.roles WHERE id=assignment.role_id;
        IF NOT ((role_code='admin' AND assignment.scope_type='national' AND assignment.scope_id='*' AND organization.org_type='headquarters')
            OR (role_code='provincial_manager' AND organization.org_type IN ('headquarters','region_company','department')
                AND assignment.scope_type='organization' AND EXISTS (SELECT 1 FROM public.organizations scope
                    WHERE scope.id::text=assignment.scope_id AND scope.status='active' AND scope.org_type='region_company'))
            OR (role_code='technician' AND organization.org_type IN ('headquarters','region_company','department')
                AND assignment.scope_type='person' AND assignment.scope_id=checked_person::text)
            OR (role_code='star_headquarters_approver' AND organization.org_type='external_approval_org'
                AND assignment.scope_type='document' AND length(btrim(assignment.scope_id))>0)) THEN
            RAISE EXCEPTION '0105 invalid effective role assignment' USING ERRCODE='23514';
        END IF;
        IF role_code IN ('admin','provincial_manager') THEN has_receiver_role:=true; END IF;
    END LOOP;
    IF NOT has_receiver_role THEN RAISE EXCEPTION '0105 receiver role required' USING ERRCODE='23514'; END IF;
    FOR permission_row IN SELECT * FROM (VALUES
        ('stock_operation','read','person',checked_person),
        ('stock_operation','read','organization',location.owner_org_id),
        ('inventory','read','organization',location.owner_org_id),
        ('stock_operation','receive_return','organization',location.owner_org_id))
        required(resource,action,scope_type,scope_id) LOOP
        WITH RECURSIVE ancestors(id,parent_id,path) AS (
            SELECT org.id,org.parent_id,ARRAY[org.id] FROM public.organizations org
                WHERE org.id=CASE WHEN permission_row.scope_type='person' THEN person.organization_id ELSE permission_row.scope_id END
                  AND org.status='active'
            UNION ALL SELECT org.id,org.parent_id,ancestors.path||org.id FROM public.organizations org
                JOIN ancestors ON ancestors.parent_id=org.id WHERE org.status='active' AND NOT org.id=ANY(ancestors.path)
        ), grants AS (
            SELECT rp.effect FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
                JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
            WHERE a.user_id=checked_actor AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
                AND a.valid_from<=current_time_value AND (a.valid_to IS NULL OR a.valid_to>current_time_value) AND r.status='active'
                AND p.resource=permission_row.resource AND p.action=permission_row.action AND p.field_code=''
                AND ((a.scope_type='national' AND a.scope_id='*')
                    OR (a.scope_type=permission_row.scope_type AND a.scope_id=permission_row.scope_id::text)
                    OR (a.scope_type='organization' AND a.scope_id IN (SELECT id::text FROM ancestors)))
        ) SELECT COALESCE(bool_or(effect='allow'),false),COALESCE(bool_or(effect='deny'),false) INTO allows,denies FROM grants;
        IF NOT allows OR denies THEN RAISE EXCEPTION '0105 receiver permission scope denied' USING ERRCODE='23514'; END IF;
    END LOOP;
END;
"""


def _timestamp(expression):
    """Pydantic's UTC datetime JSON spelling, including six-digit fractions."""
    return (f"to_char({expression} AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS') || "
        f"CASE WHEN mod(extract(microseconds FROM {expression})::bigint,1000000)=0 THEN 'Z' "
        f"ELSE to_char({expression} AT TIME ZONE 'UTC','.US\"Z\"') END")


def _file_binding():
    files = runpy.run_path(str(Path(__file__).with_name('20260926_0086_receipt_evidence_files.py')))['_files'](True)
    return files['_postgresql_binding_file_sql'](file_expression='exception.evidence_file_id',
        purpose='receipt_exception_evidence', user_expression='fact.actor_user_id',
        person_expression='fact.operator_person_id', bound_at_expression='fact.created_at', require_current_identity=True)


CHECK_BODY = """
DECLARE
    fact public.stock_operation_receipts%ROWTYPE;
    header public.receipts%ROWTYPE;
    parcel public.stock_operation_shipments%ROWTYPE;
    shipment public.shipments%ROWTYPE;
    parent public.stock_operation_orders%ROWTYPE;
    location public.stock_locations%ROWTYPE;
    source_line public.stock_operation_shipment_lines%ROWTYPE;
    line public.stock_operation_receipt_lines%ROWTYPE;
    exception public.stock_operation_receipt_exceptions%ROWTYPE;
    policy public.material_inventory_policies%ROWTYPE;
    account public.stock_accounts%ROWTYPE;
    old_view jsonb; original jsonb; package_lines jsonb := '[]'::jsonb; package jsonb;
    chosen jsonb; view jsonb; fingerprint jsonb; serials jsonb; proofs jsonb;
    accepted_serials jsonb; rejected_serials jsonb; shortage_serials jsonb;
    rejected_ids jsonb; shortage_ids jsonb; damaged_ids jsonb; exceptions jsonb;
    expected_lines jsonb := '[]'::jsonb; expected_intent jsonb; evidence jsonb; body jsonb;
    prior_accepted numeric; prior_rejected numeric; remaining numeric; amount numeric;
    ordinal integer := 0; cursor_value bigint; reference text; expected_status text;
BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0105 inventory ledger head missing' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.audit_chain_heads WHERE stream_key='material_request' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0105 audit head missing' USING ERRCODE='23514'; END IF;
    SELECT * INTO fact FROM public.stock_operation_receipts WHERE id=checked_receipt;
    IF NOT FOUND THEN RAISE EXCEPTION '0105 detached acceptance evidence' USING ERRCODE='23514'; END IF;
    SELECT * INTO header FROM public.receipts WHERE id=fact.id;
    SELECT * INTO parcel FROM public.stock_operation_shipments WHERE id=fact.shipment_id;
    SELECT * INTO shipment FROM public.shipments WHERE id=parcel.id;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=parcel.operation_id;
    SELECT * INTO location FROM public.stock_locations WHERE id=shipment.target_location_id;
    IF header.id IS NULL OR parcel.id IS NULL OR shipment.id IS NULL OR parent.id IS NULL OR location.id IS NULL
       OR header.shipment_id<>parcel.id OR header.receiver_person_id<>fact.operator_person_id
       OR fact.target_custody_assignment_id<>parcel.target_custody_assignment_id
       OR header.created_at<>fact.created_at OR fact.created_at>clock_timestamp() OR fact.created_at<parcel.created_at
       OR header.received_at>fact.created_at OR header.received_at<shipment.shipped_at
       OR fact.authorization_version<1 OR fact.audit_version<=parcel.audit_version
       OR fact.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR header.idempotency_key_hash !~ '^[a-f0-9]{64}$'
       OR header.receipt_no<>'RET-RCV-' || upper(substr(header.idempotency_key_hash,1,24))
       OR length(btrim(fact.reason,E' \t\r\n')) NOT BETWEEN 1 AND 500 OR btrim(fact.reason,E' \t\r\n')<>fact.reason
       OR jsonb_typeof(fact.command_jsonb) IS DISTINCT FROM 'object' OR jsonb_typeof(fact.plan_jsonb) IS DISTINCT FROM 'object'
       OR (SELECT count(*) FROM jsonb_object_keys(fact.plan_jsonb))<>8
       OR jsonb_typeof(fact.plan_jsonb->'lines') IS DISTINCT FROM 'array'
       OR jsonb_typeof(fact.plan_jsonb->'policies') IS DISTINCT FROM 'array'
       OR jsonb_typeof(fact.plan_jsonb->'evidence') IS DISTINCT FROM 'array'
       OR fact.plan_jsonb->'intent' IS DISTINCT FROM fact.command_jsonb
       OR fact.plan_jsonb->'authorization_version' IS DISTINCT FROM to_jsonb(fact.authorization_version)
       OR header.request_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(fact.command_jsonb),'UTF8')),'hex')
       OR fact.plan_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(fact.plan_jsonb),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0105 acceptance identity, coordinates or canonical hash mismatch' USING ERRCODE='23514';
    END IF;
    PERFORM public.rsc_check_return_receiver_0105(parcel.id,fact.actor_user_id,fact.operator_person_id,fact.authorization_version,fact.created_at);
    IF NOT EXISTS (SELECT 1 FROM public.custody_assignments WHERE id=fact.target_custody_assignment_id AND valid_from<=header.received_at) THEN
        RAISE EXCEPTION '0105 acceptance precedes custody assignment' USING ERRCODE='23514';
    END IF;
    cursor_value := (fact.plan_jsonb->>'ledger_cursor')::bigint;
    IF cursor_value IS NULL OR cursor_value<(parcel.plan_jsonb->>'ledger_cursor')::bigint
       OR fact.plan_jsonb->'ledger_cursor' IS DISTINCT FROM to_jsonb(cursor_value)
       OR cursor_value<>(SELECT next_cursor-1 FROM public.inventory_ledger_heads WHERE stream_key='inventory')
       OR fact.plan_jsonb->'audit_cursor' IS DISTINCT FROM to_jsonb(fact.audit_version-1)
       OR fact.audit_version>(SELECT version FROM public.audit_chain_heads WHERE stream_key='material_request') THEN
        RAISE EXCEPTION '0105 acceptance ledger or audit cursor mismatch' USING ERRCODE='23514';
    END IF;
    FOR source_line IN SELECT * FROM public.stock_operation_shipment_lines WHERE shipment_id=parcel.id ORDER BY line_no LOOP
        old_view := parcel.plan_jsonb->'lines'->(source_line.line_no::integer-1);
        SELECT COALESCE(jsonb_agg(jsonb_build_object('serial_id',sn.serial_id::text,'serial_no',serial.serial_no) ORDER BY sn.serial_id),'[]'::jsonb)
            INTO serials FROM public.stock_operation_shipment_serials sn JOIN public.inventory_serials serial ON serial.id=sn.serial_id
            WHERE sn.line_id=source_line.id;
        package_lines := package_lines || jsonb_build_array(jsonb_build_object('shipment_line_id',source_line.id::text,
            'outbound_no',old_view->'outbound_no','material_id',old_view->'material_id','sku_code',old_view->'sku_code',
            'material_name',old_view->'material_name','base_unit',old_view->'base_unit','condition_code',old_view->'condition_code',
            'lot_id',old_view->'lot_id','lot_no',old_view->'lot_no','shipped_quantity',to_char(source_line.quantity,'FM999999999999990.000'),
            'serials',serials));
    END LOOP;
    package := jsonb_build_object('verification_status','verified','shipment_id',parcel.id::text,'shipment_no',shipment.shipment_no,
        'operation_id',parent.id::text,'operation_no',parent.operation_no,'work_order_id',parent.oam_work_order_id::text,
        'sender_person_id',shipment.actor_person_id::text,'receiver_person_id',shipment.target_person_id::text,
        'target_location_id',location.id::text,'target_location_name',location.name,'custody_assignment_id',parcel.target_custody_assignment_id::text,
        'carrier',shipment.carrier,'tracking_no',shipment.tracking_no,'shipped_at',__SHIPPED_AT__,'recorded_at',__PARCEL_AT__,'lines',package_lines);
    IF fact.plan_jsonb->'package' IS DISTINCT FROM package OR jsonb_array_length(package_lines) NOT BETWEEN 1 AND 100
       OR jsonb_array_length(fact.plan_jsonb->'policies')<>(SELECT count(DISTINCT item->>'material_id') FROM jsonb_array_elements(package_lines) item)
       OR fact.plan_jsonb->'policies' IS DISTINCT FROM (SELECT jsonb_agg(item ORDER BY item->>0) FROM jsonb_array_elements(fact.plan_jsonb->'policies') item) THEN
        RAISE EXCEPTION '0105 original parcel projection or policy set mismatch' USING ERRCODE='23514';
    END IF;
    FOR original IN SELECT DISTINCT ON (item->>'material_id') item FROM jsonb_array_elements(package_lines) item ORDER BY item->>'material_id' LOOP
        SELECT * INTO policy FROM public.material_inventory_policies WHERE material_id=(original->>'material_id')::uuid
            AND effective_from<=fact.created_at AND (effective_to IS NULL OR effective_to>fact.created_at);
        SELECT item INTO fingerprint FROM jsonb_array_elements(fact.plan_jsonb->'policies') item WHERE item->>0=original->>'material_id';
        IF policy.id IS NULL OR fingerprint IS NULL OR jsonb_typeof(fingerprint) IS DISTINCT FROM 'array' OR jsonb_array_length(fingerprint)<>7
           OR (SELECT count(*) FROM public.material_inventory_policies WHERE material_id=policy.material_id AND effective_from<=fact.created_at
                AND (effective_to IS NULL OR effective_to>fact.created_at))<>1
           OR fingerprint->>1<>policy.id::text OR fingerprint->>2<>policy.tracking_mode
           OR fingerprint->3 IS DISTINCT FROM to_jsonb(policy.quantity_scale) OR fingerprint->4 IS DISTINCT FROM to_jsonb(policy.allow_fraction)
           OR (fingerprint->>5)::timestamptz IS DISTINCT FROM policy.effective_from
           OR (fingerprint->>6)::timestamptz IS DISTINCT FROM policy.effective_to THEN
            RAISE EXCEPTION '0105 acceptance material policy fingerprint mismatch' USING ERRCODE='23514';
        END IF;
    END LOOP;
    FOR line IN SELECT * FROM public.stock_operation_receipt_lines WHERE receipt_id=fact.id ORDER BY line_no LOOP
        ordinal := ordinal+1;
        SELECT * INTO source_line FROM public.stock_operation_shipment_lines WHERE id=line.shipment_line_id;
        SELECT item INTO original FROM jsonb_array_elements(package_lines) item WHERE item->>'shipment_line_id'=line.shipment_line_id::text;
        SELECT a.* INTO account FROM public.stock_accounts a JOIN public.stock_operation_outbound_lines departure
            ON departure.transit_stock_account_id=a.id WHERE departure.id=source_line.outbound_line_id;
        SELECT * INTO policy FROM public.material_inventory_policies WHERE material_id=account.material_id
            AND effective_from<=fact.created_at AND (effective_to IS NULL OR effective_to>fact.created_at);
        IF line.line_no<>ordinal OR line.created_at<>fact.created_at OR source_line.id IS NULL OR source_line.shipment_id<>parcel.id
           OR original IS NULL OR account.id IS NULL OR policy.id IS NULL OR account.material_id::text<>original->>'material_id'
           OR (policy.tracking_mode IN ('lot','lot_and_serial')) IS DISTINCT FROM (account.lot_id IS NOT NULL)
           OR (policy.tracking_mode IN ('serial','lot_and_serial')) IS DISTINCT FROM (jsonb_array_length(original->'serials')>0)
           OR NOT EXISTS (SELECT 1 FROM public.materials WHERE id=account.material_id AND status='active') THEN
            RAISE EXCEPTION '0105 acceptance line binding or current policy mismatch' USING ERRCODE='23514';
        END IF;
        SELECT COALESCE(sum(prior.accepted_qty),0),COALESCE(sum(prior.rejected_qty),0) INTO prior_accepted,prior_rejected
            FROM public.stock_operation_receipt_lines prior JOIN public.stock_operation_receipts r ON r.id=prior.receipt_id
            WHERE prior.shipment_line_id=line.shipment_line_id AND r.audit_version<fact.audit_version;
        remaining := source_line.quantity-prior_accepted-prior_rejected;
        IF line.accepted_qty<0 OR line.rejected_qty<0 OR line.shortage_qty<0 OR line.damaged_qty<0 OR line.damaged_qty>line.accepted_qty
           OR line.accepted_qty+line.rejected_qty+line.shortage_qty<=0 OR line.accepted_qty+line.rejected_qty+line.shortage_qty>remaining THEN
            RAISE EXCEPTION '0105 acceptance exceeds exact unconfirmed parcel budget' USING ERRCODE='23514';
        END IF;
        FOREACH amount IN ARRAY ARRAY[line.accepted_qty,line.rejected_qty,line.damaged_qty,line.shortage_qty] LOOP
            IF amount<>round(amount,policy.quantity_scale) OR (NOT policy.allow_fraction AND amount<>trunc(amount)) THEN
                RAISE EXCEPTION '0105 acceptance quantity precision mismatch' USING ERRCODE='23514';
            END IF;
        END LOOP;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('serial_id',sn.serial_id::text,'serial_no',serial.serial_no,
                'sku_code',original->'sku_code','qr_code',serial.qr_code) ORDER BY sn.serial_id) FILTER (WHERE sn.result='accepted'),'[]'::jsonb),
            COALESCE(jsonb_agg(jsonb_build_object('serial_id',sn.serial_id::text,'serial_no',serial.serial_no) ORDER BY sn.serial_id) FILTER (WHERE sn.result='accepted'),'[]'::jsonb),
            COALESCE(jsonb_agg(jsonb_build_object('serial_id',sn.serial_id::text,'serial_no',serial.serial_no) ORDER BY sn.serial_id) FILTER (WHERE sn.result='rejected'),'[]'::jsonb),
            COALESCE(jsonb_agg(jsonb_build_object('serial_id',sn.serial_id::text,'serial_no',serial.serial_no) ORDER BY sn.serial_id) FILTER (WHERE sn.result='shortage'),'[]'::jsonb),
            COALESCE(jsonb_agg(sn.serial_id::text ORDER BY sn.serial_id) FILTER (WHERE sn.result='rejected'),'[]'::jsonb),
            COALESCE(jsonb_agg(sn.serial_id::text ORDER BY sn.serial_id) FILTER (WHERE sn.result='shortage'),'[]'::jsonb),
            COALESCE(jsonb_agg(sn.serial_id::text ORDER BY sn.serial_id) FILTER (WHERE sn.damaged),'[]'::jsonb)
            INTO proofs,accepted_serials,rejected_serials,shortage_serials,rejected_ids,shortage_ids,damaged_ids
            FROM public.stock_operation_receipt_serials sn JOIN public.inventory_serials serial ON serial.id=sn.serial_id WHERE sn.line_id=line.id;
        IF (SELECT count(*) FROM public.stock_operation_receipt_serials WHERE line_id=line.id)>1000
           OR (policy.tracking_mode IN ('none','lot') AND jsonb_array_length(proofs)+jsonb_array_length(rejected_ids)+jsonb_array_length(shortage_ids)>0)
           OR (policy.tracking_mode IN ('serial','lot_and_serial') AND (jsonb_array_length(proofs)<>line.accepted_qty
                OR jsonb_array_length(rejected_ids)<>line.rejected_qty OR jsonb_array_length(shortage_ids)<>line.shortage_qty
                OR jsonb_array_length(damaged_ids)<>line.damaged_qty))
           OR EXISTS (SELECT 1 FROM public.stock_operation_receipt_serials sn
                LEFT JOIN public.inventory_serials serial ON serial.id=sn.serial_id
                LEFT JOIN public.stock_operation_shipment_serials origin ON origin.line_id=line.shipment_line_id AND origin.serial_id=sn.serial_id
                LEFT JOIN public.serial_current_positions position ON position.serial_id=sn.serial_id
                WHERE sn.line_id=line.id AND (sn.shipment_line_id<>line.shipment_line_id OR sn.created_at<>fact.created_at OR origin.id IS NULL
                    OR serial.id IS NULL OR serial.material_id<>account.material_id OR serial.lot_id IS DISTINCT FROM account.lot_id
                    OR (sn.result='accepted' AND (NOT sn.sku_verified OR NOT sn.qr_verified OR serial.lifecycle_status<>'active'
                        OR position.stock_account_id IS DISTINCT FROM account.id))
                    OR (sn.result<>'accepted' AND (sn.sku_verified OR sn.qr_verified OR sn.damaged))))
           OR EXISTS (SELECT 1 FROM public.stock_operation_receipt_serials sn JOIN public.stock_operation_receipt_serials prior
                ON prior.shipment_line_id=sn.shipment_line_id AND prior.serial_id=sn.serial_id AND prior.result IN ('accepted','rejected')
                JOIN public.stock_operation_receipt_lines prior_line ON prior_line.id=prior.line_id
                JOIN public.stock_operation_receipts prior_fact ON prior_fact.id=prior_line.receipt_id
                WHERE sn.line_id=line.id AND prior_fact.audit_version<fact.audit_version) THEN
            RAISE EXCEPTION '0105 original serial, scan or confirmed serial budget mismatch' USING ERRCODE='23514';
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('exception_type',exception_type,'description',description,
            'evidence_file_id',evidence_file_id::text) ORDER BY exception_type),'[]'::jsonb) INTO exceptions
            FROM public.stock_operation_receipt_exceptions WHERE line_id=line.id;
        IF (line.shortage_qty>0) IS DISTINCT FROM EXISTS (SELECT 1 FROM public.stock_operation_receipt_exceptions WHERE line_id=line.id AND exception_type='shortage')
           OR (line.damaged_qty>0) IS DISTINCT FROM EXISTS (SELECT 1 FROM public.stock_operation_receipt_exceptions WHERE line_id=line.id AND exception_type='damaged')
           OR (line.rejected_qty>0) IS DISTINCT FROM EXISTS (SELECT 1 FROM public.stock_operation_receipt_exceptions WHERE line_id=line.id AND exception_type IN ('rejected','wrong_material','wrong_serial')) THEN
            RAISE EXCEPTION '0105 abnormal quantities require exact exception evidence' USING ERRCODE='23514';
        END IF;
        FOR exception IN SELECT * FROM public.stock_operation_receipt_exceptions WHERE line_id=line.id LOOP
            PERFORM 1 FROM public.files WHERE id=exception.evidence_file_id FOR UPDATE;
            IF exception.receipt_id<>fact.id OR exception.created_at<>fact.created_at
               OR btrim(exception.description,E' \t\r\n')<>exception.description OR length(btrim(exception.description,E' \t\r\n')) NOT BETWEEN 1 AND 1000
               OR NOT (__FILE_BINDING__)
               OR NOT EXISTS (SELECT 1 FROM public.files WHERE id=exception.evidence_file_id
                    AND metadata_jsonb->'authorization_version'=to_jsonb(fact.authorization_version)
                    AND (metadata_jsonb->'completion'->>'verified_at')::timestamptz>=created_at)
               OR EXISTS (SELECT 1 FROM public.stock_operation_receipt_exceptions WHERE evidence_file_id=exception.evidence_file_id AND receipt_id<>fact.id)
               OR EXISTS (SELECT 1 FROM public.receipt_exceptions WHERE evidence_file_id=exception.evidence_file_id)
               OR EXISTS (SELECT 1 FROM public.material_request_files WHERE file_id=exception.evidence_file_id)
               OR EXISTS (SELECT 1 FROM public.document_attachments WHERE file_id=exception.evidence_file_id)
               OR EXISTS (SELECT 1 FROM public.approval_external_registrations WHERE evidence_file_id=exception.evidence_file_id) THEN
                RAISE EXCEPTION '0105 exception file purpose, recipient, completion or original binding mismatch' USING ERRCODE='23514';
            END IF;
        END LOOP;
        chosen := jsonb_build_object('shipment_line_id',line.shipment_line_id::text,
            'accepted_qty',to_char(line.accepted_qty,'FM999999999999990.000'),'rejected_qty',to_char(line.rejected_qty,'FM999999999999990.000'),
            'damaged_qty',to_char(line.damaged_qty,'FM999999999999990.000'),'shortage_qty',to_char(line.shortage_qty,'FM999999999999990.000'),
            'accepted_serial_verifications',proofs,'rejected_serial_ids',rejected_ids,'shortage_serial_ids',shortage_ids,
            'damaged_serial_ids',damaged_ids,'exceptions',exceptions);
        expected_lines := expected_lines || jsonb_build_array(chosen);
        view := (original-'outbound_no'-'serials'-'shipped_quantity') || (chosen-'accepted_serial_verifications'-'rejected_serial_ids'-'shortage_serial_ids')
            || jsonb_build_object('shipped_qty',original->'shipped_quantity','previously_accepted_qty',to_char(prior_accepted,'FM999999999999990.000'),
                'previously_rejected_qty',to_char(prior_rejected,'FM999999999999990.000'),'unconfirmed_qty',to_char(remaining,'FM999999999999990.000'),
                'accepted_serials',accepted_serials,'rejected_serials',rejected_serials,'shortage_serials',shortage_serials);
        IF fact.plan_jsonb->'lines'->(ordinal-1) IS DISTINCT FROM view THEN
            RAISE EXCEPTION '0105 acceptance preview line mismatch' USING ERRCODE='23514';
        END IF;
    END LOOP;
    SELECT jsonb_agg(item ORDER BY item->>'shipment_line_id') INTO expected_lines FROM jsonb_array_elements(expected_lines) item;
    expected_intent := jsonb_build_object('operation_type','receive_return','shipment_id',parcel.id::text,
        'operator_person_id',fact.operator_person_id::text,'received_at',to_char(header.received_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'reason',fact.reason,'lines',expected_lines);
    SELECT COALESCE(jsonb_agg(jsonb_build_array(file.id::text,file.sha256,file.size_bytes,file.mime_type,
        encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(file.metadata_jsonb),'UTF8')),'hex')) ORDER BY file.id),'[]'::jsonb)
        INTO evidence FROM public.files file WHERE file.id IN (SELECT evidence_file_id FROM public.stock_operation_receipt_exceptions WHERE receipt_id=fact.id);
    expected_status := CASE WHEN EXISTS (SELECT 1 FROM public.stock_operation_receipt_exceptions WHERE receipt_id=fact.id) THEN 'exception' ELSE 'accepted' END;
    reference := 'inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(fact.request_id,'UTF8')),'hex');
    IF ordinal NOT BETWEEN 1 AND 100 OR jsonb_array_length(fact.plan_jsonb->'lines')<>ordinal
       OR fact.command_jsonb IS DISTINCT FROM expected_intent OR fact.plan_jsonb->'evidence' IS DISTINCT FROM evidence
       OR header.status<>expected_status
       OR EXISTS (SELECT 1 FROM public.stock_operation_command_seals WHERE actor_user_id=fact.actor_user_id AND request_id=fact.request_id)
       OR (SELECT count(*) FROM (SELECT actor_user_id,request_id FROM public.stock_operation_orders
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_cancellations
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_outbounds
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_shipments
            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_receipts) requests
            WHERE requests.actor_user_id=fact.actor_user_id AND requests.request_id=fact.request_id)<>1
       OR EXISTS (SELECT 1 FROM public.receipt_lines WHERE receipt_id=fact.id)
       OR EXISTS (SELECT 1 FROM public.receipt_exceptions WHERE receipt_id=fact.id)
       OR EXISTS (SELECT 1 FROM public.inbound_orders WHERE receipt_id=fact.id)
       OR EXISTS (SELECT 1 FROM public.shipments WHERE idempotency_key_hash=header.idempotency_key_hash)
       OR EXISTS (SELECT 1 FROM public.inventory_transactions WHERE idempotency_key_hash=header.idempotency_key_hash
            OR (source_document_type='stock_operation_return_receipt' AND source_document_id=fact.id::text))
       OR EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='inventory' AND actor_user_id=fact.actor_user_id AND request_id=reference)
       OR EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type='inventory_transaction' AND actor_id=fact.actor_user_id
            AND metadata_jsonb->>'request_reference'=reference) THEN
        RAISE EXCEPTION '0105 acceptance cannot bypass typed history, reuse a request or post stock' USING ERRCODE='23514';
    END IF;
    body := jsonb_build_object('receipt_id',fact.id::text,'shipment_id',parcel.id::text,'operation_id',parent.id::text,
        'work_order_id',parent.oam_work_order_id::text,'operator_person_id',fact.operator_person_id::text,'status',header.status,'request_hash',header.request_hash);
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='material_request' AND actor_user_id=fact.actor_user_id AND request_id=fact.request_id)<>1
       OR NOT EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='material_request' AND stream_version=fact.audit_version
            AND aggregate_type='stock_operation_receipt' AND aggregate_id=fact.id::text AND actor_user_id=fact.actor_user_id
            AND request_id=fact.request_id AND action='stock_return_received' AND before_jsonb='{}'::jsonb AND after_jsonb=body
            AND occurred_at=fact.created_at AND created_at=fact.created_at)
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type='stock_operation_receipt' AND aggregate_id=fact.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.outbox_events WHERE aggregate_type='stock_operation_receipt' AND aggregate_id=fact.id::text
            AND event_type='stock_return_received' AND payload_jsonb=body AND idempotency_key='stock_return_received:' || fact.id::text)
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type='stock_operation_receipt' AND aggregate_id=fact.id::text)<>1
       OR NOT EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type='stock_operation_receipt' AND aggregate_id=fact.id::text
            AND from_status IS NULL AND to_status=header.status AND actor_id=fact.actor_user_id AND reason='stock_return_received'
            AND metadata_jsonb=body AND idempotency_key='stock_return_received:' || fact.id::text) THEN
        RAISE EXCEPTION '0105 complete atomic acceptance audit, state and outbox required' USING ERRCODE='23514';
    END IF;
END;
""".replace('__FILE_BINDING__', _file_binding()).replace('__SHIPPED_AT__', _timestamp('shipment.shipped_at')).replace('__PARCEL_AT__', _timestamp('parcel.created_at'))


DISPATCH_BODY = """
DECLARE identifier uuid;
BEGIN
    IF TG_TABLE_NAME='stock_operation_receipts' THEN identifier:=NEW.id;
    ELSIF TG_TABLE_NAME IN ('stock_operation_receipt_lines','stock_operation_receipt_exceptions') THEN identifier:=NEW.receipt_id;
    ELSIF TG_TABLE_NAME='stock_operation_receipt_serials' THEN
        SELECT receipt_id INTO identifier FROM public.stock_operation_receipt_lines WHERE id=NEW.line_id;
    ELSIF TG_TABLE_NAME='receipts' THEN
        IF NOT EXISTS (SELECT 1 FROM public.stock_operation_receipts WHERE id=NEW.id)
            AND NOT EXISTS (SELECT 1 FROM public.stock_operation_shipments WHERE id=NEW.shipment_id)
            AND NEW.receipt_no NOT LIKE 'RET-RCV-%' THEN RETURN NULL; END IF;
        identifier:=NEW.id;
    ELSIF TG_TABLE_NAME IN ('receipt_lines','receipt_exceptions','inbound_orders') THEN
        IF EXISTS (SELECT 1 FROM public.stock_operation_receipts WHERE id=NEW.receipt_id) THEN
            RAISE EXCEPTION '0105 return acceptance requires a complete typed inbound adapter' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    ELSIF TG_TABLE_NAME='inventory_transactions' THEN
        IF NEW.source_document_type='stock_operation_return_receipt' OR EXISTS (SELECT 1 FROM public.stock_operation_receipts fact
            JOIN public.receipts header ON header.id=fact.id WHERE header.idempotency_key_hash=NEW.idempotency_key_hash) THEN
            RAISE EXCEPTION '0105 acceptance cannot post inventory' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    ELSE
        IF NEW.aggregate_type='stock_operation_receipt' THEN identifier:=NEW.aggregate_id::uuid;
        ELSIF TG_TABLE_NAME='audit_events' THEN
            SELECT fact.id INTO identifier FROM public.stock_operation_receipts fact WHERE fact.actor_user_id=NEW.actor_user_id
                AND ((NEW.stream_key='material_request' AND fact.request_id=NEW.request_id)
                  OR (NEW.stream_key='inventory' AND NEW.request_id='inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(fact.request_id,'UTF8')),'hex')));
            IF identifier IS NULL THEN RETURN NULL; END IF;
        ELSIF TG_TABLE_NAME='state_transition_events' AND NEW.aggregate_type='inventory_transaction' THEN
            SELECT fact.id INTO identifier FROM public.stock_operation_receipts fact WHERE fact.actor_user_id=NEW.actor_id
                AND NEW.metadata_jsonb->>'request_reference'='inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(fact.request_id,'UTF8')),'hex');
            IF identifier IS NULL THEN RETURN NULL; END IF;
        ELSE RETURN NULL; END IF;
    END IF;
    IF identifier IS NULL THEN RAISE EXCEPTION '0105 detached receipt evidence' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_stock_return_receipt_0105(identifier);
    RETURN NULL;
END;
"""


def _sources():
    prior = _previous(); result = {}
    for signature in ('public.rsc_check_stock_return_0100(uuid, uuid)', 'public.rsc_check_stock_return_outbound_0103(uuid)'):
        old = prior['_sources']()[signature][1]
        anchor = 'UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_shipments) requests'
        result[signature] = (old, _replace_once(old, anchor, anchor.replace(') requests',
            '\n            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_receipts) requests')))
    old = prior['CHECK_BODY']
    anchor = 'UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_shipments) requests'
    new = _replace_once(old, anchor, anchor.replace(') requests',
        '\n            UNION ALL SELECT actor_user_id,request_id FROM public.stock_operation_receipts) requests'))
    new = _replace_once(new, 'OR EXISTS (SELECT 1 FROM public.receipts WHERE shipment_id=parcel.id)',
        'OR EXISTS (SELECT 1 FROM public.receipts receipt WHERE shipment_id=parcel.id AND NOT EXISTS (SELECT 1 FROM public.stock_operation_receipts typed WHERE typed.id=receipt.id AND typed.shipment_id=parcel.id))')
    anchor = '       OR EXISTS (SELECT 1 FROM public.inventory_transactions WHERE idempotency_key_hash=header.idempotency_key_hash'
    new = _replace_once(new, anchor, '       OR EXISTS (SELECT 1 FROM public.receipts WHERE idempotency_key_hash=header.idempotency_key_hash)\n' + anchor)
    result['public.rsc_check_stock_return_shipment_0104(uuid)'] = (old,new)
    old = prior['DISPATCH_BODY']
    anchor = "    ELSIF TG_TABLE_NAME IN ('shipment_lines','logistics_events','receipts') THEN"
    new = _replace_once(old, anchor, """    ELSIF TG_TABLE_NAME='receipts' THEN
        -- Its dedicated 0105 constraint trigger proves the complete acceptance.
        -- Do not reinterpret the historical sender's parcel at today's cursor.
        IF EXISTS (SELECT 1 FROM public.stock_operation_receipts typed WHERE typed.id=NEW.id AND typed.shipment_id=NEW.shipment_id) THEN
            RETURN NULL;
        END IF;
        IF EXISTS (SELECT 1 FROM public.stock_operation_shipments WHERE id=NEW.shipment_id) THEN
            RAISE EXCEPTION '0104 return parcels require typed downstream documents' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
""" + anchor)
    result['public.rsc_dispatch_stock_return_shipment_0104()'] = (old,new)
    old = prior['_sources']()['public.rsc_guard_stock_operation_seal_0101()'][1]
    new = _replace_once(old, "'stock_operation_shipments') THEN", "'stock_operation_shipments','stock_operation_receipts') THEN")
    new = _replace_once(new, "NOT IN ('submit_return','cancel_return','outbound_return','ship_return')",
        "NOT IN ('submit_return','cancel_return','outbound_return','ship_return','receive_return')\n       OR (seal.operation_type='receive_return') IS DISTINCT FROM (seal.shipment_id IS NOT NULL)")
    anchor = "    IF EXISTS (SELECT 1 FROM public.stock_operation_orders parent"
    new = _replace_once(new, anchor, """    IF seal.operation_type='receive_return' THEN
        IF NOT EXISTS (SELECT 1 FROM public.stock_operation_shipments parcel JOIN public.stock_operation_orders parent ON parent.id=parcel.operation_id
            WHERE parcel.id=seal.shipment_id AND parent.id=seal.operation_id AND parent.oam_work_order_id=seal.oam_work_order_id) THEN
            RAISE EXCEPTION '0105 receiver seal original parcel mismatch' USING ERRCODE='23514';
        END IF;
        PERFORM public.rsc_check_return_receiver_0105(seal.shipment_id,seal.actor_user_id,seal.operator_person_id,seal.authorization_version,seal.created_at);
        IF EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='inventory' AND actor_user_id=seal.actor_user_id AND request_id=seal.request_reference)
           OR EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type='inventory_transaction' AND actor_id=seal.actor_user_id
                AND metadata_jsonb->>'request_reference'=seal.request_reference) THEN
            RAISE EXCEPTION '0105 receipt seal has inventory evidence' USING ERRCODE='23514';
        END IF;
    END IF;
""" + anchor)
    anchor = '       OR EXISTS (SELECT 1 FROM public.audit_events event WHERE event.actor_user_id=seal.actor_user_id'
    new = _replace_once(new, anchor, """       OR EXISTS (SELECT 1 FROM public.stock_operation_receipts receipt
            WHERE receipt.actor_user_id=seal.actor_user_id AND receipt.request_id=seal.request_id)
""" + anchor)
    new = _replace_once(new, "'stock_operation_outbound','stock_operation_shipment')", "'stock_operation_outbound','stock_operation_shipment','stock_operation_receipt')")
    anchor = "    IF (SELECT count(*) FROM public.audit_events event WHERE event.stream_key='material_request'"
    new = _replace_once(new, anchor, """    IF seal.operation_type='receive_return' THEN body:=body || jsonb_build_object('shipment_id',seal.shipment_id::text); END IF;
""" + anchor)
    result['public.rsc_guard_stock_operation_seal_0101()'] = (old,new)
    files = runpy.run_path(str(Path(__file__).with_name('20260926_0086_receipt_evidence_files.py')))
    old = files['source_changes']()[('rsc_guard_formal_file_binding_0036','')][0][1]
    anchor = "    IF TG_TABLE_NAME = 'receipt_exceptions' THEN"
    new = _replace_once(old, anchor, anchor + """
        PERFORM id FROM public.files WHERE id=NEW.evidence_file_id FOR UPDATE;
        IF EXISTS (SELECT 1 FROM public.stock_operation_receipt_exceptions WHERE evidence_file_id=NEW.evidence_file_id) THEN
            RAISE EXCEPTION '0105 evidence already belongs to an original return acceptance' USING ERRCODE='23514';
        END IF;""")
    result['public.rsc_guard_formal_file_binding_0036()'] = (old,new)
    return result


FUNCTIONS = {
    ('rsc_check_return_receiver_0105','uuid, text, uuid, bigint, timestamp with time zone'):
        ('checked_shipment uuid, checked_actor text, checked_person uuid, checked_version bigint, checked_at timestamptz','void',RECEIVER_BODY),
    ('rsc_check_stock_return_receipt_0105','uuid'): ('checked_receipt uuid','void',CHECK_BODY),
    ('rsc_dispatch_stock_return_receipt_0105',''): ('','trigger',DISPATCH_BODY),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key,value in FUNCTIONS.items()}
TRIGGERS = {}
for _table in TABLES:
    TRIGGERS[f'trg_{_table}_proof_0105'] = (_table,'INSERT','rsc_dispatch_stock_return_receipt_0105',5,True)
    TRIGGERS[f'trg_{_table}_immutable_0105'] = (_table,'UPDATE OR DELETE','rsc_guard_work_order_facts_0090',27,False)
    TRIGGERS[f'trg_{_table}_no_truncate_0105'] = (_table,'TRUNCATE','rsc_guard_work_order_facts_0090',34,False)
for _table in ('receipts','receipt_lines','receipt_exceptions','inbound_orders','inventory_transactions','audit_events','outbox_events','state_transition_events'):
    TRIGGERS[f'trg_{_table}_return_receipt_0105'] = (_table,'INSERT','rsc_dispatch_stock_return_receipt_0105',5,True)
TRIGGERS['trg_stock_operation_receipts_seal_0105'] = (TABLES[0],'INSERT','rsc_guard_stock_operation_seal_0101',5,True)


def _create_tables():
    document = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
    op.create_table(TABLES[0],
        sa.Column('id',sa.Uuid(),sa.ForeignKey('receipts.id',ondelete='RESTRICT'),primary_key=True),
        sa.Column('shipment_id',sa.Uuid(),sa.ForeignKey('stock_operation_shipments.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('operator_person_id',sa.Uuid(),sa.ForeignKey('people.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('authorization_version',sa.BigInteger(),nullable=False),
        sa.Column('target_custody_assignment_id',sa.Uuid(),sa.ForeignKey('custody_assignments.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('request_id',sa.String(160),nullable=False),sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('plan_hash',sa.String(64),nullable=False),sa.Column('audit_version',sa.BigInteger(),nullable=False),
        sa.Column('command_jsonb',document,nullable=False),sa.Column('plan_jsonb',document,nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('actor_user_id','request_id',name='uq_stock_operation_receipts_request'),
        sa.UniqueConstraint('audit_version',name='uq_stock_operation_receipts_audit'),
        sa.CheckConstraint('authorization_version > 0 AND audit_version > 0',name='ck_stock_operation_receipts_versions'),
        sa.CheckConstraint('length(reason) BETWEEN 1 AND 500 AND length(plan_hash)=64',name='ck_stock_operation_receipts_context'))
    op.create_index('ix_stock_operation_receipts_shipment_id',TABLES[0],['shipment_id'])
    op.create_table(TABLES[1],
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('receipt_id',sa.Uuid(),sa.ForeignKey(TABLES[0]+'.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('shipment_line_id',sa.Uuid(),sa.ForeignKey('stock_operation_shipment_lines.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('line_no',sa.BigInteger(),nullable=False),
        *(sa.Column(name,sa.Numeric(18,3),nullable=False) for name in ('accepted_qty','rejected_qty','damaged_qty','shortage_qty')),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('receipt_id','line_no',name='uq_stock_operation_receipt_lines_order'),
        sa.UniqueConstraint('receipt_id','shipment_line_id',name='uq_stock_operation_receipt_lines_origin'),
        sa.UniqueConstraint('id','shipment_line_id',name='uq_stock_operation_receipt_lines_binding'),
        sa.UniqueConstraint('id','receipt_id',name='uq_stock_operation_receipt_lines_receipt'),
        sa.CheckConstraint('line_no > 0 AND accepted_qty >= 0 AND rejected_qty >= 0 AND shortage_qty >= 0 AND accepted_qty + rejected_qty + shortage_qty > 0 AND damaged_qty >= 0 AND damaged_qty <= accepted_qty',name='ck_stock_operation_receipt_lines_quantities'))
    op.create_index('ix_stock_operation_receipt_lines_shipment_line_id',TABLES[1],['shipment_line_id'])
    op.create_table(TABLES[2],
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('line_id',sa.Uuid(),nullable=False),
        sa.Column('shipment_line_id',sa.Uuid(),nullable=False),sa.Column('serial_id',sa.Uuid(),nullable=False),
        sa.Column('result',sa.String(24),nullable=False),
        *(sa.Column(name,sa.Boolean(),nullable=False) for name in ('damaged','sku_verified','qr_verified')),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('line_id','serial_id',name='uq_stock_operation_receipt_serials_line'),
        sa.ForeignKeyConstraint(['line_id','shipment_line_id'],[TABLES[1]+'.id',TABLES[1]+'.shipment_line_id'],ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['shipment_line_id','serial_id'],['stock_operation_shipment_serials.line_id','stock_operation_shipment_serials.serial_id'],ondelete='RESTRICT'),
        sa.CheckConstraint("result IN ('accepted','rejected','shortage') AND (NOT damaged OR result='accepted') AND ((result='accepted' AND sku_verified AND qr_verified) OR (result<>'accepted' AND NOT sku_verified AND NOT qr_verified))",name='ck_stock_operation_receipt_serials_proof'))
    op.create_index('uq_stock_operation_receipt_serials_confirmed',TABLES[2],['shipment_line_id','serial_id'],unique=True,
        postgresql_where=sa.text("result IN ('accepted','rejected')"),sqlite_where=sa.text("result IN ('accepted','rejected')"))
    op.create_table(TABLES[3],
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('line_id',sa.Uuid(),nullable=False),sa.Column('receipt_id',sa.Uuid(),nullable=False),
        sa.Column('exception_type',sa.String(32),nullable=False),sa.Column('description',sa.Text(),nullable=False),
        sa.Column('evidence_file_id',sa.Uuid(),sa.ForeignKey('files.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(['line_id','receipt_id'],[TABLES[1]+'.id',TABLES[1]+'.receipt_id'],ondelete='RESTRICT'),
        sa.UniqueConstraint('line_id','exception_type',name='uq_stock_operation_receipt_exceptions_kind'),
        sa.CheckConstraint("exception_type IN ('shortage','damaged','wrong_material','wrong_serial','rejected') AND length(description) BETWEEN 1 AND 1000",name='ck_stock_operation_receipt_exceptions_detail'))


def _permissions(upgrade):
    now=datetime.now(timezone.utc); identifier=UUID('20000000-0000-4000-8000-000000000068')
    permissions=sa.table('permissions',sa.column('id',sa.Uuid()),sa.column('resource',sa.String()),sa.column('action',sa.String()),
        sa.column('field_code',sa.String()),sa.column('description',sa.String()),sa.column('created_at',sa.DateTime()),sa.column('updated_at',sa.DateTime()))
    grants=sa.table('role_permissions',sa.column('id',sa.Uuid()),sa.column('role_id',sa.Uuid()),sa.column('permission_id',sa.Uuid()),
        sa.column('effect',sa.String()),sa.column('created_at',sa.DateTime()))
    rows=[dict(id=UUID(f'21000000-0000-4000-8000-{126+role:012d}'),role_id=UUID(f'10000000-0000-4000-8000-{role:012d}'),
        permission_id=identifier,effect='allow',created_at=now) for role in (1,2)]
    if upgrade:
        op.bulk_insert(permissions,[dict(id=identifier,resource='stock_operation',action='receive_return',field_code='',
            description='Formal recipient acceptance of exact return parcels',created_at=now,updated_at=now)])
        op.bulk_insert(grants,rows)
    else:
        op.execute(grants.delete().where(grants.c.id.in_([op.inline_literal(row['id'],type_=sa.Uuid()) for row in rows])))
        op.execute(permissions.delete().where(permissions.c.id==op.inline_literal(identifier,type_=sa.Uuid())))


def _seal_constraints(upgrade):
    kinds="'submit_return','cancel_return','outbound_return','ship_return'"+(",'receive_return'" if upgrade else '')
    origins="'cancel_return','outbound_return','ship_return'"+(",'receive_return'" if upgrade else '')
    with op.batch_alter_table('stock_operation_command_seals') as batch:
        batch.drop_constraint('ck_stock_operation_seals_type',type_='check');batch.drop_constraint('ck_stock_operation_seals_origin',type_='check')
        if upgrade:
            batch.add_column(sa.Column('shipment_id',sa.Uuid(),nullable=True))
            batch.create_foreign_key('fk_stock_operation_seals_shipment','stock_operation_shipments',['shipment_id'],['id'],ondelete='RESTRICT')
            batch.create_check_constraint('ck_stock_operation_seals_shipment',"(operation_type='receive_return') = (shipment_id IS NOT NULL)")
        else:
            batch.drop_constraint('ck_stock_operation_seals_shipment',type_='check')
            batch.drop_constraint('fk_stock_operation_seals_shipment',type_='foreignkey');batch.drop_column('shipment_id')
        batch.create_check_constraint('ck_stock_operation_seals_type',f'operation_type IN ({kinds})')
        batch.create_check_constraint('ck_stock_operation_seals_origin',f"(operation_type='submit_return' AND operation_id IS NULL) OR (operation_type IN ({origins}) AND operation_id IS NOT NULL)")
    if op.get_bind().dialect.name=='sqlite':
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_stock_operation_seals_{action.lower()}_0101 BEFORE {action} ON stock_operation_command_seals BEGIN SELECT RAISE(ABORT, '0101 return request seals are append-only'); END")


def _transition(upgrade):
    db=op.get_bind();folder=Path(__file__).parent
    if db.dialect.name not in {'postgresql','sqlite'}:raise RuntimeError('0105 supports PostgreSQL and SQLite only')
    helper=runpy.run_path(str(folder/'20260927_0087_inbound_fulfillment_boundary.py'));helper['_begin_sqlite']()
    if db.dialect.name=='postgresql':
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads, public.inventory_transactions, public.stock_operation_orders, public.stock_operation_cancellations, public.stock_operation_outbounds, public.stock_operation_shipments, public.stock_operation_command_seals, public.receipts, public.receipt_lines, public.receipt_exceptions, public.inbound_orders, public.files, public.audit_events IN SHARE ROW EXCLUSIVE MODE')
    if not upgrade:
        if db.dialect.name=='postgresql':op.execute('LOCK TABLE '+','.join('public.'+table for table in TABLES)+' IN SHARE ROW EXCLUSIVE MODE')
        helper['_preflight'](' OR '.join(f'EXISTS (SELECT 1 FROM {table})' for table in TABLES)+" OR EXISTS (SELECT 1 FROM stock_operation_command_seals WHERE operation_type='receive_return')",
            '0105 downgrade blocked: immutable return acceptance or request seals must be retained')
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
                    THEN RAISE EXCEPTION '0105 function source, ownership or ACL drift'; END IF; END $body$""")
        for signature,(old,new) in _sources().items():
            replace(signature=signature,expected_hash=hashlib.sha256((old if upgrade else new).encode()).hexdigest(),
                replacement_hash=hashlib.sha256((new if upgrade else old).encode()).hexdigest(),
                replacements=((old,new),) if upgrade else ((new,old),),label='stock_return_receipt_0105')
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),label='stock_return_receipt_readiness_0105')
        if not upgrade:
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name,signature in reversed(FUNCTIONS):op.execute(f'DROP FUNCTION public.{name}({signature})')
    if db.dialect.name=='sqlite' and upgrade:
        for table in TABLES:
            for action in ('UPDATE','DELETE'):
                op.execute(f"CREATE TRIGGER trg_{table}_{action.lower()}_0105 BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, '0105 return acceptances are append-only'); END")
    if not upgrade:
        _permissions(False)
        for table in reversed(TABLES):op.drop_table(table)


def upgrade():_transition(True)
def downgrade():_transition(False)
