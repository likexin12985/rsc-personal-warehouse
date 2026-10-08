"""Reviewed shipment projection SQL, frozen with revision 0168.

This axis records carrier handover, not quantity completion or receipt. A
partial package is shipped too; remaining quantities stay on the line facts.
"""


def evidence_sql(schema='public.', request='NEW', current_command=False):
    version = (f'command.target_version = {request}.version'
               if current_command else f'command.target_version <= {request}.version')
    timing = f'AND command.occurred_at = {request}.updated_at' if current_command else ''
    return f"""EXISTS (
        SELECT 1 FROM {schema}shipments shipment
        JOIN {schema}material_request_commands command
          ON command.idempotency_key_hash = shipment.idempotency_key_hash
        JOIN {schema}shipment_lines line ON line.shipment_id = shipment.id
        JOIN {schema}outbound_postings outbound ON outbound.id = line.outbound_posting_id
        WHERE outbound.request_id = {request}.id AND line.shipped_qty > 0
          AND shipment.status = 'shipped' AND command.operation = 'shipment'
          AND command.request_id = {request}.id AND {version}
          AND command.request_hash = shipment.request_hash
          AND command.occurred_at = shipment.created_at {timing}
    )"""


def state_sql(schema='public.', request='NEW'):
    return f"CASE WHEN {evidence_sql(schema, request)} THEN 'shipped' ELSE 'not_started' END"


REQUEST_OLD = """    IF NEW.shipment_status <> 'not_started'
       OR NEW.logistics_signature_status <> 'not_signed'"""
REQUEST_NEW = f"""    IF NEW.shipment_status IS DISTINCT FROM ({state_sql()})
       OR (NEW.shipment_status IS DISTINCT FROM OLD.shipment_status AND (
           NEW.version <> OLD.version + 1 OR NEW.updated_at <= OLD.updated_at
           OR NOT {evidence_sql(current_command=True)})) THEN
        RAISE EXCEPTION '0168 shipment projection does not match handover command' USING ERRCODE = '23514';
    END IF;
    IF NEW.logistics_signature_status <> 'not_signed'"""
SUPPLY_OLD = "       OR request_row.shipment_status <> 'not_started'"
SUPPLY_NEW = f"       OR request_row.shipment_status IS DISTINCT FROM ({state_sql(request='request_row')})"
AXIS_OLD = """           OR command_row.result_jsonb->'state_axes'->>'shipment_status' <>
               request_row.shipment_status"""
AXIS_NEW = """           OR command_row.result_jsonb->'state_axes'->>'shipment_status' <>
               'not_started'"""


def changes():
    return {
        'rsc_guard_material_request_identity_0029()': ((REQUEST_OLD, REQUEST_NEW),),
        'rsc_validate_material_request_supply_causality_0059(uuid, bigint)':
            ((SUPPLY_OLD, SUPPLY_NEW), (AXIS_OLD, AXIS_NEW)),
        'rsc_oam_runtime_binding_ready_0044()': (('20261216_0167', '20261217_0168'),),
    }
